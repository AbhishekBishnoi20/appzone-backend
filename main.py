from fastapi import FastAPI, HTTPException, Depends, Request
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
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from chat_handlers import ChatCompletionHandler
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


async def get_model_config(model_name, has_image=False):
    # Choose base URL and collection based on whether request has image
    if has_image:
        base_url = GITHUB_BASE_URL
        collection_name = "github_keys"
    else:
        base_url = CHATANYWHERE_BASE_URL
        collection_name = "chatanywhere_keys"
    
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

chat_handler = ChatCompletionHandler(SYSTEM_PROMPT, COT_SYSTEM_PROMPT)

@app.post("/v1/chat/completions")
@limiter.limit("10/minute")
async def chat_completions(
    request: Request,
    payload: dict, 
    api_key: str = Depends(get_api_key)
):
    _ = request  # Used by rate limiter
    logger.info("Received request")

    try:
        model_name = payload.get("model", "gpt-4o")
        logger.info(f"Fetching configuration for model: {model_name}")
        
        # Check if the request contains an image
        has_image = any(
            message.get("role") == "user" and 
            isinstance(message.get("content"), list) and
            any(item.get("type") == "image_url" for item in message.get("content", []))
            for message in payload.get("messages", [])
        )
        
        base_url, api_key = await get_model_config(model_name, has_image)
        logger.info(f"Model configuration obtained - Base URL: {base_url}, API Key: {api_key}")
    except ValueError as e:
        logger.error(f"Error fetching model configuration: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to fetch model configuration")

    logger.info("Returning StreamingResponse")
    return StreamingResponse(
        chat_handler.process_chat_completion(payload, base_url, api_key),
        media_type="text/event-stream"
    )

@app.get("/")
async def root():
    return {"message": "Server is running"}

@app.get("/prompts", response_class=HTMLResponse)
async def get_prompts():
    conn = sqlite3.connect('prompts.db')
    cursor = conn.cursor()
    cursor.execute('SELECT id, prompt, image_urls, timestamp FROM prompts ORDER BY timestamp DESC')
    prompts = cursor.fetchall()
    conn.close()

    # Group prompts by date
    grouped_prompts = defaultdict(list)
    for id, prompt, image_urls, timestamp in prompts:
        date = datetime.fromisoformat(timestamp).date()
        image_list = json.loads(image_urls) if image_urls else []
        grouped_prompts[date].append((id, prompt, image_list))

    template = Template('''
    <html>
        <head>
            <title>User Prompts</title>
            <style>
                body {
                    font-family: Arial, sans-serif;
                    max-width: 800px;
                    margin: 0 auto;
                    padding: 20px;
                }
                h1 {
                    color: #333;
                    border-bottom: 2px solid #eee;
                    padding-bottom: 10px;
                }
                .date, .back-button {
                    cursor: pointer;
                    margin: 20px 0;
                    padding: 10px;
                    background-color: #e0e0e0;
                    border-radius: 5px;
                    text-align: center;
                }
                .prompts {
                    list-style-type: none;
                    padding: 0;
                }
                .prompts li {
                    padding: 15px;
                    margin: 10px 0;
                    background-color: #f9f9f9;
                    border-radius: 5px;
                    border: 1px solid #eee;
                }
                .prompts li:hover {
                    background-color: #f0f0f0;
                }
                .hidden {
                    display: none;
                }
                .image-link {
                    display: inline-block;
                    margin: 5px;
                    padding: 5px 10px;
                    background-color: #007bff;
                    color: white;
                    text-decoration: none;
                    border-radius: 3px;
                    font-size: 0.9em;
                }
                .image-link:hover {
                    background-color: #0056b3;
                }
                .prompt-container {
                    margin-bottom: 15px;
                }
                .image-container {
                    margin-top: 10px;
                }
                .delete-btn {
                    background-color: #dc3545;
                    color: white;
                    border: none;
                    padding: 5px 10px;
                    border-radius: 3px;
                    cursor: pointer;
                    margin-left: 10px;
                }
                .delete-btn:hover {
                    background-color: #c82333;
                }
                .date-actions {
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                }
                .prompt-header {
                    display: flex;
                    justify-content: space-between;
                    align-items: flex-start;
                }
            </style>
            <script>
                function showPrompts(date) {
                    document.getElementById('dates').classList.add('hidden');
                    document.getElementById('prompts-' + date).classList.remove('hidden');
                    document.getElementById('back-button').classList.remove('hidden');
                    // Set the URL hash when showing prompts
                    window.location.hash = date;
                }

                function showDates() {
                    document.getElementById('dates').classList.remove('hidden');
                    document.querySelectorAll('.prompts').forEach(el => el.classList.add('hidden'));
                    document.getElementById('back-button').classList.add('hidden');
                    // Clear the URL hash when showing dates
                    window.location.hash = '';
                }

                // Check URL hash on page load
                window.onload = function() {
                    const date = window.location.hash.slice(1); // Remove the # symbol
                    if (date && document.getElementById('prompts-' + date)) {
                        showPrompts(date);
                    }
                };

                async function deletePrompt(id, date) {
                    if (confirm('Are you sure you want to delete this prompt?')) {
                        try {
                            const response = await fetch(`/prompts/${id}`, {
                                method: 'DELETE'
                            });
                            if (response.ok) {
                                document.getElementById(`prompt-${id}`).remove();
                                // If no more prompts for this date, hide the date
                                const datePrompts = document.querySelectorAll(`#prompts-${date} li`);
                                if (datePrompts.length === 0) {
                                    document.getElementById(`date-${date}`).remove();
                                    document.getElementById(`prompts-${date}`).remove();
                                }
                            } else {
                                alert('Failed to delete prompt');
                            }
                        } catch (error) {
                            alert('Error deleting prompt');
                        }
                    }
                }

                async function deleteDate(date) {
                    if (confirm('Are you sure you want to delete all prompts for this date?')) {
                        try {
                            const response = await fetch(`/prompts/date/${date}`, {
                                method: 'DELETE'
                            });
                            if (response.ok) {
                                document.getElementById(`date-${date}`).remove();
                                document.getElementById(`prompts-${date}`).remove();
                                showDates();
                            } else {
                                alert('Failed to delete date');
                            }
                        } catch (error) {
                            alert('Error deleting date');
                        }
                    }
                }
            </script>
        </head>
        <body>
            <h1>User Prompts</h1>
            <div id="back-button" class="back-button hidden" onclick="showDates()">Back to Dates</div>
            <div id="dates">
                {% for date in grouped_prompts.keys() %}
                    <div id="date-{{ date }}" class="date-actions">
                        <div class="date" onclick="showPrompts('{{ date }}')">
                            {{ date }}
                        </div>
                        <button class="delete-btn" onclick="deleteDate('{{ date }}')">Delete All</button>
                    </div>
                {% endfor %}
            </div>
            {% for date, prompts in grouped_prompts.items() %}
                <ul class="prompts hidden" id="prompts-{{ date }}">
                    {% for id, prompt, images in prompts %}
                        <li id="prompt-{{ id }}" class="prompt-container">
                            <div class="prompt-header">
                                <div class="prompt-text">{{ prompt }}</div>
                                <button class="delete-btn" onclick="deletePrompt({{ id }}, '{{ date }}')">Delete</button>
                            </div>
                            {% if images %}
                                <div class="image-container">
                                    {% for image_url in images %}
                                        <a href="{{ image_url }}" target="_blank" class="image-link">
                                            View Image {{ loop.index }}
                                        </a>
                                    {% endfor %}
                                </div>
                            {% endif %}
                        </li>
                    {% endfor %}
                </ul>
            {% endfor %}
        </body>
    </html>
    ''')
    return template.render(grouped_prompts=grouped_prompts)

@app.delete("/prompts/date/{date}")
async def delete_date_prompts(date: str):
    try:
        conn = sqlite3.connect('prompts.db')
        cursor = conn.cursor()
        cursor.execute('DELETE FROM prompts WHERE DATE(timestamp) = ?', (date,))
        conn.commit()
        conn.close()
        return {"message": f"All prompts for {date} deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/prompts/{prompt_id}")
async def delete_prompt(prompt_id: int):
    try:
        conn = sqlite3.connect('prompts.db')
        cursor = conn.cursor()
        cursor.execute('DELETE FROM prompts WHERE id = ?', (prompt_id,))
        conn.commit()
        conn.close()
        return {"message": f"Prompt {prompt_id} deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
