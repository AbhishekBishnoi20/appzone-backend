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

from PyPDF2 import PdfReader
from io import BytesIO
import tiktoken
from document_handlers.extractor import DocumentExtractor
from base.db.api_key import get_api_key
from base.db.service import update_service_metrics
from base.db.connection import init_db, close_db
from base.db.endpoint import get_all_endpoints, update_table_stats
from base.db.reset import reset_all_today_columns
from apscheduler.schedulers.asyncio import AsyncIOScheduler

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



limiter = Limiter(
    key_func=get_remote_address
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

@app.on_event("startup")
async def startup_event():
    await init_db()
    # Initialize the scheduler
    scheduler = AsyncIOScheduler()
    # Add the reset job to run daily at midnight
    scheduler.add_job(reset_all_today_columns, trigger='cron', hour=0, minute=0)
    scheduler.start()

@app.on_event("shutdown")
async def shutdown_event():
    await close_db()

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

        # Get all available endpoints
        endpoints = await get_all_endpoints()
        if not endpoints:
            raise HTTPException(status_code=500, detail="No endpoints available")

        # Use the first available endpoint
        selected_endpoint = endpoints[0]
        base_url = selected_endpoint["base_url"]
        endpoint_api_key = selected_endpoint["api_key"]

        # Track service metrics
        await update_service_metrics("/v1/chat/completions", True)
        await update_table_stats(selected_endpoint["table_name"], selected_endpoint["key_id"], model_name, 200)

        return StreamingResponse(
            handler.process_chat_completion(payload, base_url, endpoint_api_key),
            media_type="text/event-stream"
        )
    except Exception as e:
        # Track failed request
        await update_service_metrics("/v1/chat/completions", False)
        await update_table_stats(selected_endpoint["table_name"], selected_endpoint["key_id"], model_name, 500)
        logger.error(f"Error in chat_completions: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/")
async def root():
    return {"message": "Server is running"}

@app.post("/report-messages")
@limiter.limit("10/minute")
async def report_messages(
    request: Request,
    api_key: str = Depends(get_api_key)
):
    try:
        payload = await request.json()
        message = payload.get('message', '')
        logger.info(f"Reported Message: {message}")

        # Track successful request
        await update_service_metrics("/report-messages", True)
        return {"status": "success"}
    except Exception as e:
        # Track failed request
        await update_service_metrics("/report-messages", False)
        logger.error(f"Error processing report: {e}")
        raise HTTPException(status_code=400, detail="Invalid report")

@app.post("/extract-text")
@limiter.limit("5/minute")
async def extract_text(
    request: Request,
    file: UploadFile = File(...),
    api_key: str = Depends(get_api_key)
):
    try:
        extractor = DocumentExtractor()
        text, file_type = await extractor.extract_text(file)

        # Track successful request
        await update_service_metrics("/extract-text", True)

        return {
            "text": text,
            "file_type": file_type,
            "filename": file.filename,
            "tokens": len(extractor.encoding.encode(text))
        }
    except Exception as e:
        # Track failed request
        await update_service_metrics("/extract-text", False)
        raise HTTPException(status_code=500, detail=str(e))
