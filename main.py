from fastapi import FastAPI, HTTPException, Depends
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import StreamingResponse
import aiohttp
from dotenv import load_dotenv
import logging
import json
import os

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

# Remove these lines:
# POCKETBASE_URL = os.getenv("POCKETBASE_URL", "http://pocketbase-vhkachra.appzone.tech/api/")
# ADMIN_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJleHAiOjE3MzA5ODA2MDQsImlkIjoid3h2ODE0ZTZ3NDRndmM0IiwidHlwZSI6ImFkbWluIn0.k7sX6xXd5EmRp8xw7s6Az9Q4inpB-nZBCnhrZAA_FP8"

MODEL_CONFIGS = {
    "gpt-4o-mini": {
        "base_url": "https://api.chatanywhere.com.cn/v1",
        "api_key": "sk-pu4PasDkEf284PIbVr1r5jn9rlvbAJESZGpPbK7OFYYR6m9g"
    },
    "gpt-4o": {
        "base_url": "https://apis.chatfire.cn/v1",
        "api_key": "sk-tfoO5ew6EIK9WbMlE7A5012832274593912e3e79De734198"
    }
}

async def get_model_config(model_name):
    if model_name not in MODEL_CONFIGS:
        raise ValueError(f"No configuration found for model: {model_name}")
    
    config = MODEL_CONFIGS[model_name]
    return config["base_url"], config["api_key"]

@app.post("/v1/chat/completions")
async def chat_completions(payload: dict, api_key: str = Depends(get_api_key)):
    logger.info("Received request")

    try:
        model_name = payload.get("model", "gpt-4o")
        logger.info(f"Fetching configuration for model: {model_name}")
        
        base_url, api_key = await get_model_config(model_name)
        logger.info(f"Model configuration obtained - Base URL: {base_url}")
    except ValueError as e:
        logger.error(f"Error fetching model configuration: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to fetch model configuration")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async def stream_response() -> AsyncGenerator[str, None]:
        async with aiohttp.ClientSession() as session:
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
