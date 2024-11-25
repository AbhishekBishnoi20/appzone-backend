from fastapi import FastAPI, HTTPException, Depends
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import StreamingResponse
import aiohttp
from dotenv import load_dotenv
import logging
import json
import os
import asyncio
import random

from typing import AsyncGenerator

load_dotenv()

app = FastAPI()

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

# Authentication
API_KEYS = [
    "az-initial-key",  # Existing hardcoded API key
    "az-test-key"      # New hardcoded API key
]
api_key_header = APIKeyHeader(name="Authorization", auto_error=False)

async def get_api_key(api_key_header: str = Depends(api_key_header)):
    if not api_key_header or not any(api_key_header == f"Bearer {key}" for key in API_KEYS):
        logger.warning(f"Invalid API Key: {api_key_header}")
        raise HTTPException(status_code=401, detail="Invalid API Key")
    return api_key_header

# PocketBase configuration
POCKETBASE_URL = os.getenv("POCKETBASE_URL", "https://pocketbase-forapp.appsettle.com/api/")
ADMIN_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJleHAiOjE3MzM1OTU3NTcsImlkIjoiN3djczBrOG9mNzBja284IiwidHlwZSI6ImFkbWluIn0.zY3fw9d87bdM4XXT8FG3padDidjRIPJMpPXEc7LwK7o"
SYSTEM_PROMPT = """
You are ChatGPT, a large language model trained by OpenAI, based on the GPT-4 architecture.
You are chatting with the user via the ChatGPT Android app. This means most of the time your lines should be a sentence or two, unless the user's request requires reasoning or long-form outputs. Never use emojis unless explicitly asked to. Do not use any special text formatting. Never use # or ### for headings. Never use asterisks for bold or italic text. Never use backticks for code blocks. Never use hyphens or asterisks for bullet points. Never use > for quotes. Never use square brackets or parentheses for links. Never use numbered lists with dots.
All responses must be in plain text only with regular numbers and letters. Use simple line breaks to separate paragraphs when needed.
"""
# Add these constants at the top with other configurations
# CHATANYWHERE_BASE_URL = "https://api.chatanywhere.com.cn/v1"
GITHUB_BASE_URL = "https://models.inference.ai.azure.com"

async def get_model_config(model_name, has_image=False):
    # Always use GitHub URL and keys regardless of model
    base_url = GITHUB_BASE_URL
    collection_name = "github_keys"
    
    headers = {
        "Authorization": ADMIN_TOKEN
    }
    
    # Fetch all available keys
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{POCKETBASE_URL}collections/{collection_name}/records", headers=headers) as response:
            if response.status == 200:
                data = await response.json()
                if data.get("items") and len(data["items"]) > 0:
                    # Randomly select one key from available keys
                    item = random.choice(data["items"])
                    return base_url, item.get("api_key")
    
    raise ValueError(f"No API keys found for collection: {collection_name}")

@app.post("/v1/chat/completions")
async def chat_completions(payload: dict, api_key: str = Depends(get_api_key)):
    logger.info("Received request")

    # Always use gpt-4o-mini regardless of requested model
    original_model = payload.get("model", "")
    payload["model"] = "gpt-4o-mini"
    logger.info(f"Original model request: {original_model}, Using: gpt-4o-mini")

    # Check if the request contains an image
    has_image = False
    messages = payload.get("messages", [])
    for message in messages:
        if message.get("role") == "user":
            content = message.get("content", [])
            if isinstance(content, list):
                for item in content:
                    if item.get("type") == "image_url":
                        has_image = True
                        break

    # Add system prompt to the messages
    system_prompt = {
        "role": "system",
        "content": SYSTEM_PROMPT
    }
    
    # Create a new messages array with system prompt followed by user messages
    payload["messages"] = [system_prompt] + messages

    try:
        model_name = payload.get("model", "gpt-4o")
        logger.info(f"Fetching configuration for model: {model_name}, has_image: {has_image}")
        
        base_url, api_key = await get_model_config(model_name, has_image)
        logger.info(f"Model configuration obtained - Base URL: {base_url}")
    except ValueError as e:
        logger.error(f"Error fetching model configuration: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to fetch model configuration")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async def stream_response() -> AsyncGenerator[str, None]:
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
                                            first_chunk = False
                                        elif finish_reason == "stop":
                                            transformed_data["choices"][0]["delta"] = {}
                                        else:
                                            transformed_data["choices"][0]["delta"] = {
                                                "content": choice.get("delta", {}).get("content", "")
                                            }
                                        
                                        transformed_data["choices"][0]["finish_reason"] = finish_reason
                                    
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

    logger.info("Returning StreamingResponse")
    return StreamingResponse(stream_response(), media_type="text/event-stream")

@app.get("/")
async def root():
    return {"message": "Server is running"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
