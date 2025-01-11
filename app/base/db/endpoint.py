from .connection import execute_query, single_fetch_query, get_table_columns, fetch_query
from datetime import datetime, timezone

async def update_table_stats(table_name: str, key_id: str, model: str, status: int):

    try:
        # Get current timestamp in UTC
        current_time = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3] + 'Z'

        # Get table columns
        columns = await get_table_columns(table_name)

        # Prepare model column name (replace - with _)
        model_column = f"today_{model.replace('-', '_')}"

        # Build update query parts
        update_parts = [
            "today_requests = today_requests + 1",
            "requests = requests + 1",
            "status = ?",
            "updated = ?"
        ]
        params = [status, current_time]

        # If model-specific column exists, include it in update
        if model_column in columns:
            update_parts.insert(0, f"{model_column} = {model_column} + 1")

        # Construct final query
        update_query = f"""
            UPDATE {table_name}
            SET {', '.join(update_parts)}
            WHERE id = ?
        """
        params.append(key_id)

        # Execute update
        await execute_query(update_query, params)

    except Exception as e:
        raise Exception(f"Failed to update table stats: {e}")



async def get_all_endpoints():
    """
    Fetch all endpoints from the Endpoints table.

    Returns:
        list: A list of dictionaries containing endpoint information
    """
    try:
        query = """
            SELECT id, table_name, base_url, api_key, key_id, used
            FROM Endpoints
        """

        endpoints = await fetch_query(query)
        return endpoints
    except Exception as e:
        raise Exception(f"Failed to fetch endpoints: {e}")