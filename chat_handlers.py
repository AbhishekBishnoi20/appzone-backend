import json
import logging
import asyncio
import aiohttp
from typing import AsyncGenerator
from fastapi import HTTPException
import sqlite3
from datetime import datetime

# Set up logging
logger = logging.getLogger(__name__)

class ChatCompletionHandler:
    def __init__(self, system_prompt, cot_system_prompt):
        self.SYSTEM_PROMPT = system_prompt
        self.COT_SYSTEM_PROMPT = cot_system_prompt
        self.db_connection = sqlite3.connect('prompts.db', check_same_thread=False)
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
        """Extract prompt content and image data from messages."""
        if not messages:
            return "", []
        
        first_message = messages[0].get("content", "")
        image_data = []
        text_content = ""
        
        # Handle string content
        if isinstance(first_message, str):
            return first_message, image_data
        
        # Handle list content (multimodal input)
        if isinstance(first_message, list):
            for item in first_message:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        text_content += item.get("text", "") + " "
                    elif item.get("type") == "image_url":
                        image_url = item.get("image_url", {}).get("url", "")
                        if image_url:
                            image_data.append(image_url)
        
        return text_content.strip(), image_data

    async def process_chat_completion(self, payload: dict, base_url: str, api_key: str) -> AsyncGenerator[str, None]:
        # Extract and save the user prompt and image data
        messages = payload.get("messages", [])
        text_content, image_urls = self._extract_prompt_content(messages)
        await self.save_prompt(text_content, image_urls)
        
        # Always use gpt-4o-mini regardless of requested model
        original_model = payload.get("model", "")
        payload["model"] = "gpt-4o-mini"
        logger.info(f"Original model request: {original_model}, Using: gpt-4o-mini")

        # Check if the request contains an image
        messages = payload.get("messages", [])
        
        # Choose the appropriate system prompt based on model
        selected_system_prompt = self.COT_SYSTEM_PROMPT if original_model == "o1" else self.SYSTEM_PROMPT
        
        system_prompt = {
            "role": "system",
            "content": selected_system_prompt
        }
        
        # Create a new messages array with system prompt followed by user messages
        payload["messages"] = [system_prompt] + messages

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
                async with session.post(api_url, json=payload, headers=headers) as response:
                    logger.info(f"Received response with status: {response.status}")
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"Error response from API: {error_text}")
                        yield f"data: {{\"error\": \"API returned non-200 status: {response.status}\"}}\n\n"
                        return

                    first_chunk = True
                    async for line in response.content:
                        if line:
                            line = line.decode('utf-8').strip()
                            if line.startswith("data: "):
                                data = line[6:].strip()
                                logger.info(f"Received from API: {data}")
                                
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
                                        logger.info(f"Sent to client: {response_to_send.strip()}")
                                        
                                        if finish_reason == "stop":
                                            return

                                except json.JSONDecodeError:
                                    logger.error(f"Failed to parse JSON: {data}")

                    # If we've reached this point, the stream ended without a "stop" finish_reason
                    yield "data: [DONE]\n\n"
                    logger.info("Sent to client: [DONE]")

            except asyncio.TimeoutError:
                logger.error("Request timed out after 5 minutes")
                yield f"data: {{\"error\": \"Request timed out after 5 minutes\"}}\n\n"
            except aiohttp.ClientError as e:
                logger.error(f"Error in stream_response: {str(e)}")
                yield f"data: {{\"error\": \"{str(e)}\"}}\n\n"
            except Exception as e:
                logger.error(f"Unexpected error in stream_response: {str(e)}")
                yield f"data: {{\"error\": \"Unexpected error occurred\"}}\n\n"

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