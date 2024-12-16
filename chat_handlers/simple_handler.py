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
                async with session.post(api_url, json=payload, headers=headers) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"Error response from API: {error_text}")
                        yield f"data: {{\"error\": \"API returned non-200 status: {response.status}\"}}\n\n"
                        yield "data: [DONE]\n\n"
                        return

                    first_chunk = True
                    async for line in response.content:
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
                logger.error(f"Unexpected error: {str(e)}")
                yield f"data: {{\"error\": \"{str(e)}\"}}\n\n"
                yield "data: [DONE]\n\n"
