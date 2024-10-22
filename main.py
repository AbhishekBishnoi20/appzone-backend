from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import StreamingResponse
import httpx
import os
from dotenv import load_dotenv
import logging
import json

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
OPENAI_API_URL = "https://api.proxyapi.ru/openai/v1/chat/completions"
endpoint_key = "sk-VKa9joRd7jw8szImPTxBaji0HV6ioHpW"
OPENAI_API_KEY = "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"
OPENAI_MODERATION_URL = "https://api.openai.com/v1/moderations"

async def check_moderation(text: str) -> dict:
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "input": text,
        "model": "text-moderation-latest"
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(OPENAI_MODERATION_URL, json=payload, headers=headers)
        if response.status_code != 200:
            logger.error(f"Moderation API error: {response.text}")
            raise HTTPException(status_code=500, detail="Error checking content moderation")
        result = response.json()
        logger.info(f"Raw moderation API response: {json.dumps(result)}")
        return result["results"][0]

@app.post("/v1/chat/completions")
async def chat_completions(payload: dict, api_key: str = Depends(get_api_key)):
    logger.info(f"Received request with payload: {json.dumps(payload)}")
    
    # Extract only the user's latest message for moderation
    user_messages = [msg for msg in payload.get("messages", []) if msg["role"] == "user"]
    if not user_messages:
        raise HTTPException(status_code=400, detail="No user message found")
    
    text_to_moderate = user_messages[-1]["content"]
    if isinstance(text_to_moderate, list):
        # If content is a list, extract text from it
        text_to_moderate = " ".join([item.get("text", "") for item in text_to_moderate if item.get("type") == "text"])
    
    logger.info(f"Text to moderate: {text_to_moderate}")
    
    # Check moderation
    moderation_result = await check_moderation(text_to_moderate)
    logger.info(f"Full moderation API response: {json.dumps(moderation_result)}")
    
    is_flagged = moderation_result.get("flagged", False)
    logger.info(f"Is content flagged: {is_flagged}")
    
    if is_flagged:
        logger.warning(f"Content flagged by moderation: {text_to_moderate}")
        logger.warning(f"Moderation details: {json.dumps(moderation_result)}")
        return JSONResponse(
            status_code=400,
            content={"error": "Content flagged as inappropriate", "details": moderation_result}
        )
    
    # If content is not flagged, proceed with chat completion
    headers = {
        "Authorization": f"Bearer {endpoint_key}",
        "Content-Type": "application/json",
    }

    async def stream_response():
        async with httpx.AsyncClient() as client:
            try:
                async with client.stream("POST", OPENAI_API_URL, json=payload, headers=headers) as response:
                    async for chunk in response.aiter_bytes():
                        yield chunk
            except Exception as e:
                logger.error(f"Error streaming response: {str(e)}")
                raise HTTPException(status_code=500, detail="Error streaming response")

    return StreamingResponse(stream_response(), media_type="text/event-stream")

@app.get("/")
async def root():
    return {"message": "Server is running"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
