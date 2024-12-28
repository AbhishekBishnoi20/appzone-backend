import json
import logging
import aiohttp
from typing import AsyncGenerator
from .base_handler import BaseChatHandler, FormatPlaceholder
from datetime import datetime

logger = logging.getLogger(__name__)

class SimpleChatHandler(BaseChatHandler):
    async def process_chat_completion(self, payload: dict, base_url: str, api_key: str) -> AsyncGenerator[str, None]:
        messages = payload.get("messages", [])
        text_content, image_urls = self._extract_prompt_content(messages)
        await self.save_prompt(text_content, image_urls)
        
        selected_system_prompt = self.COT_SYSTEM_PROMPT if payload.get("model") == "o1" else self.SYSTEM_PROMPT
        
        truncated_messages = self._truncate_messages(messages, selected_system_prompt)
        
        format_dict = {"datetime_now": datetime.now().strftime("%d %B %Y")}
        current_system_prompt = selected_system_prompt.format_map(FormatPlaceholder(format_dict))
        
        payload["messages"] = [{"role": "system", "content": current_system_prompt}] + truncated_messages
        payload["model"] = "gpt-4o-mini"
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        timeout = aiohttp.ClientTimeout(total=300, connect=30)
        
        async with aiohttp.ClientSession(timeout=timeout) as session:
            try:
                api_url = f"{base_url}/chat/completions"
                logger.info(f"Sending request to {api_url}")
                
                async with session.post(api_url, json=payload, headers=headers) as response:
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

                    first_chunk = True
                    full_response = []  # Collect full response for logging
                    
                    async for line in response.content:
                        if line:
                            line = line.decode('utf-8').strip()
                            if line.startswith("data: "):
                                data = line[6:].strip()
                                try:
                                    json_data = json.loads(data)
                                    transformed_data = self._transform_response(json_data, first_chunk)
                                    first_chunk = False
                                    
                                    # Collect response for logging
                                    if 'choices' in json_data and json_data['choices']:
                                        content = json_data['choices'][0].get('delta', {}).get('content', '')
                                        if content:
                                            full_response.append(content)
                                    
                                    choices = json_data.get("choices", [])
                                    if choices:
                                        finish_reason = choices[0].get("finish_reason")
                                        
                                        response_to_send = f"data: {json.dumps(transformed_data)}\n\n"
                                        if finish_reason == "stop":
                                            response_to_send += "data: [DONE]\n\n"
                                        yield response_to_send
                                        
                                        if finish_reason == "stop":
                                            # Log the complete response after sending everything to user
                                            logger.info(f"Complete response: {''.join(full_response)}")
                                            return
                                except json.JSONDecodeError:
                                    logger.error(f"Failed to parse JSON: {data}")

                    yield "data: [DONE]\n\n"

            except Exception as e:
                logger.error(f"Unexpected error: {str(e)}")
                yield f"data: {{\"error\": \"{str(e)}\"}}\n\n"
                yield "data: [DONE]\n\n"
