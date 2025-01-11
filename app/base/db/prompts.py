import datetime
from .connection import execute_query, fetch_query, single_fetch_query, does_table_exist, get_table_columns
import json


async def store_prompt(prompt: str, image_urls: list):
    image_urls = json.dumps(image_urls)
    now = datetime.datetime.utcnow().isoformat()
    sql = f"""
        INSERT INTO Prompts (prompt, image_urls, created, updated)
        VALUES ( ?, ?, ?, ?)
    """
    params = ( prompt, image_urls, now, now)
    await execute_query(sql, params)