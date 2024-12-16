import json
import logging
import asyncio
import sqlite3
import tiktoken
from datetime import datetime

logger = logging.getLogger(__name__)

class FormatPlaceholder(dict):
    def __missing__(self, key):
        return "{" + key + "}"

class BaseChatHandler:
    def __init__(self, system_prompt, cot_system_prompt):
        self.SYSTEM_PROMPT = system_prompt
        self.COT_SYSTEM_PROMPT = cot_system_prompt
        self.MAX_INPUT_TOKENS = 8000
        self.encoding = tiktoken.get_encoding("cl100k_base")
        self.db_connection = sqlite3.connect('prompts.db', check_same_thread=False)
        self._setup_database()

    def _setup_database(self):
        with self.db_connection:
            self.db_connection.execute('''
                CREATE TABLE IF NOT EXISTS prompts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    prompt TEXT NOT NULL,
                    image_urls TEXT,
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
        if not messages:
            return "", []
        
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
        
        if isinstance(content, str):
            return content, image_data
        
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
        return len(self.encoding.encode(text))

    def _truncate_messages(self, messages: list, system_prompt: str) -> list:
        total_tokens = self._count_tokens(system_prompt)
        available_tokens = self.MAX_INPUT_TOKENS - 500
        truncated_messages = []
        
        if messages:
            latest_message = messages[-1]
            content = latest_message.get("content", "")
            if isinstance(content, list):
                content_tokens = sum(self._count_tokens(item.get("text", "")) 
                                  for item in content 
                                  if item.get("type") == "text")
            else:
                content_tokens = self._count_tokens(str(content))
            
            total_tokens += content_tokens
            truncated_messages.insert(0, latest_message)
        
        for message in reversed(messages[:-1]):
            content = message.get("content", "")
            if isinstance(content, list):
                content_tokens = sum(self._count_tokens(item.get("text", "")) 
                                  for item in content 
                                  if item.get("type") == "text")
            else:
                content_tokens = self._count_tokens(str(content))
            
            if total_tokens + content_tokens <= available_tokens:
                total_tokens += content_tokens
                truncated_messages.insert(0, message)
            else:
                break
        
        return truncated_messages

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
