from fastapi import FastAPI, HTTPException, Depends, Request, File, UploadFile
from fastapi.security import APIKeyHeader, HTTPBasic, HTTPBasicCredentials
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
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from chat_handlers.simple_handler import SimpleChatHandler
from chat_handlers.function_handler import FunctionChatHandler
from config import (
    SYSTEM_PROMPT, 
    COT_SYSTEM_PROMPT, 
    POCKETBASE_URL, 
    ADMIN_TOKEN, 
    API_KEYS,
    GITHUB_BASE_URL,
    CHATANYWHERE_BASE_URL
)
from fastapi.responses import HTMLResponse
from jinja2 import Template
import sqlite3
from collections import defaultdict
from datetime import datetime
from prompt_routes import router as prompt_router
from PyPDF2 import PdfReader
from io import BytesIO
import tiktoken
from document_handlers.extractor import DocumentExtractor

load_dotenv()

app = FastAPI(docs_url=None, redoc_url=None)

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


async def get_model_config(model_name, has_image=False, is_function_handler=False):
    # Choose base URL and collection based on handler type
    if is_function_handler:
        base_url = CHATANYWHERE_BASE_URL
        collection_name = "chatanywhere_keys"
    else:
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

limiter = Limiter(
    key_func=get_remote_address
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

@app.post("/v1/chat/completions")
@limiter.limit("10/minute")
async def chat_completions(
    request: Request,
    payload: dict,
    api_key: str = Depends(get_api_key)
):
    try:
        version_header = request.headers.get("X-App-Version")
        is_function_handler = version_header == "2.0"
        
        if is_function_handler:
            handler = FunctionChatHandler(SYSTEM_PROMPT, COT_SYSTEM_PROMPT)
        else:
            handler = SimpleChatHandler(SYSTEM_PROMPT, COT_SYSTEM_PROMPT)
        
        model_name = payload.get("model", "gpt-4o")
        has_image = any(
            message.get("role") == "user" and 
            isinstance(message.get("content"), list) and
            any(item.get("type") == "image_url" for item in message.get("content", []))
            for message in payload.get("messages", [])
        )
        
        base_url, api_key = await get_model_config(model_name, has_image, is_function_handler)
        
        return StreamingResponse(
            handler.process_chat_completion(payload, base_url, api_key),
            media_type="text/event-stream"
        )
    except Exception as e:
        logger.error(f"Error in chat_completions: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
async def root():
    return {"message": "Server is running"}

@app.post("/extract-text")
@limiter.limit("5/minute")
async def extract_text(
    request: Request,
    file: UploadFile = File(...)
):
    """Universal route for text extraction from various file types."""
    extractor = DocumentExtractor()
    text, file_type = await extractor.extract_text(file)
    return {
        "text": text,
        "file_type": file_type,
        "filename": file.filename,
        "tokens": len(extractor.encoding.encode(text))
    }

app.include_router(prompt_router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
