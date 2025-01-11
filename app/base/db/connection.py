import aiosqlite
import os
import random
import re
from datetime import datetime

pool = None
DATABASE_URL = os.getenv('DB_PATH')


async def create_connection():
    try:
        db_path = DATABASE_URL
        # Create the directory if it doesn't exist
        return await aiosqlite.connect(db_path)
    except Exception as e:
        raise Exception(f"Failed to create a database connection: {e}")


async def init_db():
    global pool
    pool = await create_connection()


async def close_db():
    global pool
    await pool.close()


async def execute_query(sql, params=None):
    await check_pool()
    async with pool.cursor() as cursor:
        await cursor.execute(sql, params or ())
        await pool.commit()
        return cursor.lastrowid

async def execute_query_with_return(sql, params=None):
    await check_pool()
    async with pool.cursor() as cursor:
        await cursor.execute(sql, params or ())
        await pool.commit()

        if sql.strip().upper().startswith("INSERT"):
            # Extract the table name from the INSERT statement
            table_name = extract_table_name_from_insert(sql)
            if table_name:
                await cursor.execute(f"SELECT id FROM {table_name} WHERE rowid = last_insert_rowid()")
                inserted_id = await cursor.fetchone()
                return inserted_id[0] if inserted_id else None

        return cursor.lastrowid

def extract_table_name_from_insert(sql):
    # A regex to capture the table name from an INSERT INTO statement
    match = re.search(r"INSERT\s+INTO\s+([^\s(]+)", sql, re.IGNORECASE)
    return match.group(1) if match else None


async def fetch_query(sql, params=None):
    await check_pool()
    async with pool.execute(sql, params or ()) as cursor:
        rows = await cursor.fetchall()
        columns = [description[0] for description in cursor.description]
        result = [dict(zip(columns, row)) for row in rows]
        return result


async def single_fetch_query(sql, params=None):
    await check_pool()
    async with pool.execute(sql, params or ()) as cursor:
        row = await cursor.fetchone()
        if row:
            columns = [description[0] for description in cursor.description]
            result = dict(zip(columns, row))
            return result
        return None


async def does_table_exist(table_name):
    result = await single_fetch_query("SELECT name FROM sqlite_master WHERE type='table' AND name=?;", (table_name,))
    return result is not None


async def check_pool():
    global pool
    if pool is None:
        await init_db()

async def get_table_columns(table_name):
    """Get all column names for a given table"""
    sql = f"PRAGMA table_info({table_name})"
    columns = await fetch_query(sql)
    return [col['name'] for col in columns]
