from .connection import execute_query, fetch_query, get_table_columns
from datetime import datetime, timezone
import re

async def reset_daily_stats():
    """
    Resets the 'today_requests' and 'today_stability' columns in 'apps', 'users', and 'service' tables.
    """
    current_time = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3] + 'Z'

    # Reset stats for 'apps' table
    await execute_query("UPDATE apps SET today_requests = 0, updated = ?", (current_time,))

    # Reset stats for 'users' table
    await execute_query("UPDATE users SET today_requests = 0, updated = ?", (current_time,))

    # Reset stats for 'service' table
    await execute_query("UPDATE service SET today_requests = 0, today_stability = 0, updated = ?", (current_time,))


async def reset_all_today_columns():
    """
    Resets all columns starting with 'today_' in all tables.
    """
    current_time = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3] + 'Z'

    tables = await fetch_query("SELECT name FROM sqlite_master WHERE type='table';")

    for table in tables:
        table_name = table['name']
        columns = await get_table_columns(table_name)
        today_columns = [col for col in columns if re.match(r'^today_', col)]

        if today_columns:
            set_statements = ', '.join([f"{col} = 0" for col in today_columns])
            update_query = f"UPDATE {table_name} SET {set_statements}, updated = ?"
            await execute_query(update_query, (current_time,))
