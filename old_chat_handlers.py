import json
import logging
import asyncio
import aiohttp
from typing import AsyncGenerator
from fastapi import HTTPException
import sqlite3
from datetime import datetime
import tiktoken
from config import tools, SYSTEM_PROMPT, COT_SYSTEM_PROMPT
from tools.dalle import dalle_generate
from tools.imgbb import upload_to_imgbb
from tools.search import tavily_search
from tools.retreive import retrieve_tool
# Set up logging
logger = logging.getLogger(__name__)

class FormatPlaceholder(dict):
    def __missing__(self, key):
        return "{" + key + "}"

class ChatCompletionHandler:
    def __init__(self, system_prompt, cot_system_prompt):
        self.SYSTEM_PROMPT = system_prompt
        self.COT_SYSTEM_PROMPT = cot_system_prompt
        self.MAX_INPUT_TOKENS = 8000
        self.encoding = tiktoken.get_encoding("cl100k_base")  # GPT-4 encoding
        self.db_connection = sqlite3.connect('prompts.db', check_same_thread=False)
        self.available_functions = {
            "dalle": dalle_generate,
            "browser_search": tavily_search,
            "open_url": retrieve_tool
        }
        self._setup_database()

    def _setup_database(self):
        with self.db_connection:
            self.db_connection.execute('''
                CREATE TABLE IF NOT EXISTS prompts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    prompt TEXT NOT NULL,
                    image_urls TEXT,  -- Store URLs as JSON string
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')

    async def save_prompt(self, prompt: str, image_urls: list):
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._insert_prompt, prompt, image_urls)

    def _insert_prompt(self, prompt: str, image_urls: list):
        with self.db_connection:
            self.db_connection.execute(
                'INSERT INTO prompts (prompt, image_urls, timestamp) VALUES (?, ?, CURRENT_TIMESTAMP)',
                (prompt, json.dumps(image_urls))
            )

    def _extract_prompt_content(self, messages):
        """Extract the latest user prompt content and image data from messages."""
        if not messages:
            return "", []
        
        # Find the last user message
        last_user_message = None
        for message in reversed(messages):
            if message.get("role") == "user":
                last_user_message = message
                break
        
        if not last_user_message:
            return "", []
        
        content = last_user_message.get("content", "")
        image_data = []
        text_content = ""
        
        # Handle string content
        if isinstance(content, str):
            return content, image_data
        
        # Handle list content (multimodal input)
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        text_content += item.get("text", "") + " "
                    elif item.get("type") == "image_url":
                        image_url = item.get("image_url", {}).get("url", "")
                        if image_url:
                            image_data.append(image_url)
        
        return text_content.strip(), image_data

    def _count_tokens(self, text: str) -> int:
        """Count the number of tokens in a text string."""
        return len(self.encoding.encode(text))

    def _truncate_messages(self, messages: list, system_prompt: str) -> list:
        """Truncate messages to fit within token limit while preserving recent context."""
        # Start with system prompt tokens
        total_tokens = self._count_tokens(system_prompt)
        
        # Reserve some tokens for the response (500 for safety buffer)
        available_tokens = self.MAX_INPUT_TOKENS - 500
        
        # Process messages from newest to oldest
        truncated_messages = []
        
        # Always include the most recent message
        if messages:
            latest_message = messages[-1]
            content = latest_message.get("content", "")
            if isinstance(content, list):  # Handle multimodal content
                content_tokens = sum(self._count_tokens(item.get("text", "")) 
                                  for item in content 
                                  if item.get("type") == "text")
            else:
                content_tokens = self._count_tokens(str(content))
            
            total_tokens += content_tokens
            truncated_messages.insert(0, latest_message)
        
        # Process remaining messages from newest to oldest
        for message in reversed(messages[:-1]):
            content = message.get("content", "")
            if isinstance(content, list):
                content_tokens = sum(self._count_tokens(item.get("text", "")) 
                                  for item in content 
                                  if item.get("type") == "text")
            else:
                content_tokens = self._count_tokens(str(content))
            
            # Check if adding this message would exceed the limit
            if total_tokens + content_tokens <= available_tokens:
                total_tokens += content_tokens
                truncated_messages.insert(0, message)
            else:
                # If we can't add more messages, break
                break
        
        logger.info(f"Total tokens after truncation: {total_tokens}")
        return truncated_messages

    async def process_tool_call(self, tool_call):
        function_name = tool_call['function']['name']
        function_to_call = self.available_functions[function_name]
        function_args = json.loads(tool_call['function']['arguments'])
        
        logger.info(f"Calling {function_name} with args: {function_args}")
        
        try:
            function_response = await function_to_call(**function_args)
            logger.info(f"Received response from {function_name}")
            
            if function_name == "dalle":
                try:
                    # Send image data event directly
                    yield f"data: {json.dumps({'type': 'image_url' ,'data': function_response})}\n\n"
                    
                    # Use a placeholder for the message content to reduce payload size
                    yield {
                        "tool_call_id": tool_call['id'],
                        "role": "tool",
                        "name": function_name,
                        "content": json.dumps({
                            "content": [
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "data": "image_already_sent"  # Short placeholder
                                    }
                                }
                            ]
                        })
                    }
                except Exception as e:
                    logger.error(f"Failed to process image data: {str(e)}")
                    yield {
                        "tool_call_id": tool_call['id'],
                        "role": "tool",
                        "name": function_name,
                        "content": f"Error processing image: {str(e)}"
                    }
            else:
                # For non-DALLE tools, yield the response directly
                yield {
                    "tool_call_id": tool_call['id'],
                    "role": "tool",
                    "name": function_name,
                    "content": function_response,
                }

        except Exception as e:
            logger.error(f"Error in tool call: {str(e)}")
            yield {
                "tool_call_id": tool_call['id'],
                "role": "tool",
                "name": function_name,
                "content": f"Error: {str(e)}",
            }

    async def process_chat_completion(self, payload: dict, base_url: str, api_key: str) -> AsyncGenerator[str, None]:
        # Extract and save the user prompt and image data
        messages = payload.get("messages", [])
        text_content, image_urls = self._extract_prompt_content(messages)
        await self.save_prompt(text_content, image_urls)
        
        # Choose the appropriate system prompt
        selected_system_prompt = self.COT_SYSTEM_PROMPT if payload.get("model") == "o1" else self.SYSTEM_PROMPT
        
        # Truncate messages to fit token limit
        truncated_messages = self._truncate_messages(messages, selected_system_prompt)
        
        # Create a dictionary with just the date format
        format_dict = {"datetime_now": datetime.now().strftime("%d %B %Y")}
        
        # Use safe_format to only replace datetime_now and ignore other curly braces
        current_system_prompt = selected_system_prompt.format_map(FormatPlaceholder(format_dict))
        
        # Create payload with truncated messages
        payload["messages"] = [{"role": "system", "content": current_system_prompt}] + truncated_messages
        
        # Always use gpt-4o-mini regardless of requested model
        original_model = payload.get("model", "")
        payload["model"] = "gpt-4o-mini"
        logger.info(f"Original model request: {original_model}, Using: gpt-4o-mini")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        # Set 5 minute total timeout
        timeout = aiohttp.ClientTimeout(
            total=300,  # 5 minutes total timeout
            connect=30  # 30 seconds for initial connection
        )
        
        async with aiohttp.ClientSession(timeout=timeout) as session:
            try:
                api_url = f"{base_url}/chat/completions"
                logger.info(f"Sending request to {api_url}")
                
                if "tools" not in payload:
                    payload["tools"] = tools
                    payload["tool_choice"] = "auto"

                first_payload = payload.copy()
                first_payload["stream"] = False
                
                async with session.post(api_url, json=first_payload, headers=headers) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"Error response from API: {error_text}")
                        yield f"data: {{\"error\": \"API returned non-200 status: {response.status}\"}}\n\n"
                        yield "data: [DONE]\n\n"
                        return

                    response_json = await response.json()
                    logger.info(f"First response: {response_json}")
                    message = response_json.get("choices", [{}])[0].get("message", {})
                    tool_calls = message.get("tool_calls", [])
                    
                    if tool_calls:
                        logger.info(f"Found tool calls: {tool_calls}")
                        
                        # Send tool call event with tool name and size if it's dalle
                        for tool_call in tool_calls:
                            tool_name = tool_call['function']['name']
                            event_data = {'type': 'tool_call', 'tool': tool_name}
                            
                            # Add size for dalle calls
                            if tool_name == 'dalle':
                                try:
                                    args = json.loads(tool_call['function']['arguments'])
                                    event_data['size'] = args.get('size', '1024x1024')  # Default size if not specified
                                except json.JSONDecodeError:
                                    logger.error("Failed to parse dalle arguments")
                            
                            yield f"data: {json.dumps(event_data)}\n\n"
                        
                        # Add the assistant's message with tool calls
                        messages.append(message)
                        
                        # Process tool calls and collect responses
                        tool_responses = []  # Initialize the tool_responses list
                        for tool_call in tool_calls:
                            response = None
                            # Process all events from the tool call
                            async for event in self.process_tool_call(tool_call):
                                if isinstance(event, str):  # This is a stream event (like image URL)
                                    yield event
                                else:  # This is the final tool response
                                    response = event
                            
                            if response:
                                tool_responses.append(response)
                                messages.append(response)
                        
                        # Check for browser_search and open_url results and send status
                        for tool_call, response in zip(tool_calls, tool_responses):
                            if tool_call['function']['name'] == 'browser_search':
                                args = json.loads(tool_call['function']['arguments'])
                                max_results = args.get('max_results', 0)
                                
                                status_event = {
                                    "type": "tool_status",
                                    "tool": "browser_search",
                                    "details": f"Searched {max_results} sites",
                                }
                                logger.info(f"Sending browser search status: {status_event}")
                                yield f"data: {json.dumps(status_event)}\n\n"
                                await asyncio.sleep(0.1)
                            
                            elif tool_call['function']['name'] == 'open_url':
                                args = json.loads(tool_call['function']['arguments'])
                                url = args.get('url', '')
                                truncated_url = url[:20] + "..." if len(url) > 20 else url
                                
                                status_event = {
                                    "type": "tool_status",
                                    "tool": "open_url",
                                    "details": f"Reading {truncated_url}"
                                }
                                logger.info(f"Sending open_url status: {status_event}")
                                yield f"data: {json.dumps(status_event)}\n\n"
                                await asyncio.sleep(0.1)
                        
                        # Add DALLE instruction if needed
                        if any(tc.get("function", {}).get("name") == "dalle" for tc in tool_calls):
                            messages.append({
                                "role": "user",
                              # "content": "you've created and user received the image. Please just give a sentence. Do not use any markdown"
                                "content": "Please just give a sentence. Do not use any markdown"
                            })
                        
                        logger.info(f"Making second API call with messages: {messages}")
                        
                        # Make second API call WITH streaming
                        second_payload = {
                            "model": "gpt-4o-mini",
                            "messages": messages,
                            "stream": True
                        }
                        
                        async with session.post(api_url, json=second_payload, headers=headers) as second_response:
                            first_chunk = True
                            async for line in second_response.content:
                                if line:
                                    line = line.decode('utf-8').strip()
                                    if line.startswith("data: "):
                                        data = line[6:].strip()
                                        try:
                                            json_data = json.loads(data)
                                            transformed_data = self._transform_response(json_data, first_chunk)
                                            first_chunk = False
                                            
                                            choices = json_data.get("choices", [])
                                            if choices:
                                                finish_reason = choices[0].get("finish_reason")
                                                
                                                response_to_send = f"data: {json.dumps(transformed_data)}\n\n"
                                                if finish_reason == "stop":
                                                    response_to_send += "data: [DONE]\n\n"
                                                yield response_to_send
                                                
                                                if finish_reason == "stop":
                                                    return
                                        except json.JSONDecodeError:
                                            logger.error(f"Failed to parse JSON: {data}")
                    else:
                        # No tool calls, stream the original response
                        payload["stream"] = True
                        async with session.post(api_url, json=payload, headers=headers) as stream_response:
                            first_chunk = True
                            async for line in stream_response.content:
                                if line:
                                    line = line.decode('utf-8').strip()
                                    if line.startswith("data: "):
                                        data = line[6:].strip()
                                        try:
                                            json_data = json.loads(data)
                                            transformed_data = self._transform_response(json_data, first_chunk)
                                            first_chunk = False
                                            
                                            choices = json_data.get("choices", [])
                                            if choices:
                                                finish_reason = choices[0].get("finish_reason")
                                                
                                                response_to_send = f"data: {json.dumps(transformed_data)}\n\n"
                                                if finish_reason == "stop":
                                                    response_to_send += "data: [DONE]\n\n"
                                                yield response_to_send
                                                
                                                if finish_reason == "stop":
                                                    return
                                        except json.JSONDecodeError:
                                            logger.error(f"Failed to parse JSON: {data}")

                # If we reach here without seeing a stop reason, send [DONE]
                yield "data: [DONE]\n\n"

            except Exception as e:
                logger.error(f"Unexpected error: {str(e)}")
                yield f"data: {{\"error\": \"{str(e)}\"}}\n\n"
                yield "data: [DONE]\n\n"

    def _transform_response(self, json_data: dict, first_chunk: bool) -> dict:
        transformed_data = {
            "id": json_data.get("id", ""),
            "object": "chat.completion.chunk",
            "created": json_data.get("created", 0),
            "model": json_data.get("model", ""),
            "system_fingerprint": json_data.get("system_fingerprint", ""),
            "choices": [{
                "index": 0,
                "delta": {},
                "logprobs": None,
                "finish_reason": None
            }]
        }
        
        choices = json_data.get("choices", [])
        if choices:
            choice = choices[0]
            finish_reason = choice.get("finish_reason")
            
            if first_chunk:
                transformed_data["choices"][0]["delta"] = {
                    "role": "assistant",
                    "content": choice.get("delta", {}).get("content", "")
                }
            elif finish_reason == "stop":
                transformed_data["choices"][0]["delta"] = {}
            else:
                transformed_data["choices"][0]["delta"] = {
                    "content": choice.get("delta", {}).get("content", "")
                }
            
            transformed_data["choices"][0]["finish_reason"] = finish_reason
        
        return transformed_data 

