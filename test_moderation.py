import asyncio
import httpx
import os
from dotenv import load_dotenv
import json

load_dotenv()

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
            print(f"Moderation API error: {response.text}")
            return None
        result = response.json()
        return result["results"][0]

async def test_moderation():
    test_cases = [
        "I love programming!",
        "I want to kill somebody",
        "The weather is nice today",
        "I hate everyone and want to hurt them",
    ]

    for text in test_cases:
        result = await check_moderation(text)
        print(f"\nInput: {text}")
        print(f"Moderation result: {json.dumps(result, indent=2)}")
        if result["flagged"]:
            print("This content was flagged as inappropriate.")
        else:
            print("This content was not flagged.")

if __name__ == "__main__":
    asyncio.run(test_moderation())
