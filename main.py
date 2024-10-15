from fastapi import FastAPI, HTTPException, Depends
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import StreamingResponse
import httpx
import os
from dotenv import load_dotenv
import logging

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
API_KEY = os.getenv("API_KEY", "az-initial-key")  # Hardcoded API key
api_key_header = APIKeyHeader(name="Authorization", auto_error=False)

async def get_api_key(api_key_header: str = Depends(api_key_header)):
    if not api_key_header or api_key_header != f"Bearer {API_KEY}":
        logger.warning(f"Invalid API Key: {api_key_header}")
        raise HTTPException(status_code=401, detail="Invalid API Key")
    return api_key_header

# OpenAI API details
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

@app.post("/chat/completions")
async def chat_completions(payload: dict, api_key: str = Depends(get_api_key)):
    logger.info(f"Received request with payload: {payload}")
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
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
