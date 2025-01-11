# chat_handlers/function_handler.py

from .base_handler import BaseChatHandler, FormatPlaceholder
from datetime import datetime
import json
import logging
import aiohttp
from typing import AsyncGenerator
from config import tools
from tools.dalle import dalle_generate
from tools.search import tavily_search
from tools.retreive import retrieve_tool
import asyncio
from fastapi import HTTPException
from base.db.prompts import store_prompt
logger = logging.getLogger(__name__)

class FunctionChatHandler(BaseChatHandler):
    def __init__(self, system_prompt, cot_system_prompt):
        super().__init__(system_prompt, cot_system_prompt)
        self.available_functions = {
            "dalle": dalle_generate,
            "browser_search": tavily_search,
            "open_url": retrieve_tool
        }

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

                    # Return the tool response as a dict (not as a string!)
                    yield {
                        "tool_call_id": tool_call['id'],
                        "role": "tool",
                        "name": function_name,
                        "content": json.dumps({
                            "content": [
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "data": "image_already_sent"
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
        try:
            # Extract and save the user prompt and image data
            messages = payload.get("messages", [])
            text_content, image_urls = self._extract_prompt_content(messages)
            await store_prompt(text_content, image_urls)

            selected_system_prompt = self.COT_SYSTEM_PROMPT if payload.get("model") == "o1" else self.SYSTEM_PROMPT
            truncated_messages = self._truncate_messages(messages, selected_system_prompt)

            # Transform document type messages to text type
            transformed_messages = self._transform_document_messages(truncated_messages)

            format_dict = {"datetime_now": datetime.now().strftime("%d %B %Y")}
            current_system_prompt = selected_system_prompt.format_map(FormatPlaceholder(format_dict))

            payload["messages"] = [{"role": "system", "content": current_system_prompt}] + transformed_messages
            payload["model"] = "gpt-4o-mini"

            if "tools" not in payload:
                payload["tools"] = tools
                payload["tool_choice"] = "auto"
                payload["parallel_tool_calls"] = False

            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }

            timeout = aiohttp.ClientTimeout(total=300, connect=30)

            async with aiohttp.ClientSession(timeout=timeout) as session:
                api_url = f"{base_url}/chat/completions"
                logger.info(f"Sending request to {api_url}")

                # Make first non-streaming call
                first_payload = payload.copy()
                first_payload["stream"] = False

                async with session.post(api_url, json=first_payload, headers=headers) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"Error response from API: {error_text}")

                        # Check specifically for content management policy error
                        if "content management policy" in error_text.lower():
                            transformed_data = {
                                "id": "error",
                                "object": "chat.completion.chunk",
                                "created": int(datetime.now().timestamp()),
                                "model": payload.get("model", ""),
                                "choices": [{
                                    "index": 0,
                                    "delta": {
                                        "role": "assistant",
                                        "content": "⚠️ The content may trigger our content management policy. Please modify your prompt and retry."
                                    },
                                    "finish_reason": None
                                }]
                            }
                            yield f"data: {json.dumps(transformed_data)}\n\n"

                            # Send final chunk with finish_reason
                            transformed_data["choices"][0]["delta"] = {}
                            transformed_data["choices"][0]["finish_reason"] = "stop"
                            yield f"data: {json.dumps(transformed_data)}\n\n"
                            yield "data: [DONE]\n\n"
                            return

                        # For all other errors, use the fixed error message
                        error_message = "An error occurred while processing your request. Please try again later."
                        yield f"data: {{\"error\": \"{error_message}\"}}\n\n"
                        yield "data: [DONE]\n\n"
                        return

                    response_json = await response.json()
                    logger.info(f"First response: {response_json}")
                    message = response_json.get("choices", [{}])[0].get("message", {})
                    tool_calls = message.get("tool_calls", [])

                    if tool_calls:
                        logger.info(f"Found tool calls: {tool_calls}")

                        # Send tool call events immediately without delay
                        for tool_call in tool_calls:
                            tool_name = tool_call['function']['name']
                            event_data = {'type': 'tool_call', 'tool': tool_name}

                            if tool_name == 'dalle':
                                try:
                                    args = json.loads(tool_call['function']['arguments'])
                                    event_data['size'] = args.get('size', '1024x1024')
                                except json.JSONDecodeError:
                                    logger.error("Failed to parse dalle arguments")

                            yield f"data: {json.dumps(event_data)}\n\n"

                        messages.append(message)

                        # Process tool calls and collect responses without delays
                        tool_responses = []
                        for tool_call in tool_calls:
                            response = None
                            async for event in self.process_tool_call(tool_call):
                                if isinstance(event, str):
                                    yield event
                                else:
                                    response = event

                            if response:
                                tool_responses.append(response)
                                messages.append(response)

                        # Send status events immediately
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

                        # Add DALLE instruction if needed
                        if any(tc.get("function", {}).get("name") == "dalle" for tc in tool_calls):
                            messages.append({
                                "role": "user",
                                "content": "Please just give a sentence. Do not use any markdown and never say you cannot create images"
                            })

                        # Make second streaming call
                        transformed_messages = self._transform_document_messages(messages)
                        second_payload = {
                            "model": "gpt-4o-mini",
                            "messages": transformed_messages,
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
                        # Handle non-tool calls (streaming)
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

                yield "data: [DONE]\n\n"

        except Exception as e:
            logger.error(f"Error in process_chat_completion: {str(e)}")
            yield f"data: {{\"error\": \"{str(e)}\"}}\n\n"
            yield "data: [DONE]\n\n"