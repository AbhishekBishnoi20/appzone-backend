from fastapi import FastAPI, HTTPException, Depends
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import StreamingResponse
import httpx
import os
from dotenv import load_dotenv
import logging
import json
import asyncio
import orjson  # Add this import at the top of your file
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

# OpenAI API details
OPENAI_API_URL = "https://app.appzone.tech/v1/chat/completions"
OPENAI_API_KEY = "az-initial-key"

@app.post("/v1/chat/completions")
async def chat_completions(payload: dict, api_key: str = Depends(get_api_key)):
    logger.info("Received request")

    transformed_payload = {
        "model": payload.get("model", "gpt-4o-mini"),
        "messages": [
            {
                "role": "system",
                "content": [{"type": "text", "text": "You are ChatGPT, a large language model trained by OpenAI, based on the GPT-4 architecture. You are chatting with the user via the Android app. This means most of the time your lines should be a sentence or two, unless the user's request requires reasoning or long-form outputs. Never use emojis, unless explicitly asked to. Avoid markdown."}]
            }
        ] + payload.get("messages", []),
        "stream": payload.get("stream", True)
    }

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    async def stream_response() -> AsyncGenerator[str, None]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                async with client.stream("POST", OPENAI_API_URL, json=transformed_payload, headers=headers) as response:
                    first_chunk = True
                    async for line in response.aiter_lines():
                        if line and line.startswith("data: "):
                            data = line[6:].strip()
                            logger.info(f"Received from AppZone: {data}")
                            
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

            except (httpx.RemoteProtocolError, httpx.ReadTimeout) as e:
                logger.error(f"Stream ended unexpectedly: {str(e)}")
            except Exception as e:
                logger.error(f"Error in stream_response: {str(e)}")

    return StreamingResponse(stream_response(), media_type="text/event-stream")

@app.get("/")
async def root():
    return {"message": "Server is running"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
