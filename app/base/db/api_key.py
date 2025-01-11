from .connection import single_fetch_query, execute_query, does_table_exist
from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from datetime import datetime, timezone

security = HTTPBearer()

async def get_api_key(credentials: HTTPAuthorizationCredentials = Security(security)) -> str:
    """
    Dependency function to get and validate API key from Authorization header
    """
    try:
        # Extract API key from Bearer token
        api_key = credentials.credentials

        # Verify the API key and get user/app data
        data = await verify_api_key(api_key)

        # Update request counts
        await update_request_count(data)

        return api_key
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error processing API key: {str(e)}"
        )

async def update_request_count(data: dict) -> None:
    """Update request counts and timestamp for the given app/user"""
    table_name = 'apps' if 'name' in data else 'users'
    current_time = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3] + 'Z'

    update_query = f"""
        UPDATE {table_name}
        SET today_requests = today_requests + 1,
            requests = requests + 1,
            updated = ?
        WHERE id = ?
    """

    await execute_query(update_query, (current_time, data['id']))

async def verify_api_key(api_key: str) -> dict:
    if not api_key:
        raise HTTPException(status_code=401, detail="API key is required")

    # Check apps table first
    app_data = await single_fetch_query(
        "SELECT * FROM apps WHERE api_key = ?",
        (api_key,)
    )
    if app_data:
        return app_data

    # Check users table if app not found
    user_data = await single_fetch_query(
        "SELECT * FROM users WHERE api_key = ?",
        (api_key,)
    )
    if user_data:
        return user_data

    raise HTTPException(
        status_code=401,
        detail="Invalid API key"
    )

async def authenticate_api_key(api_key: str) -> bool:
    """
    Verify API key and update request counts if valid.
    Returns True if key is valid, raises HTTPException otherwise.
    """
    try:
        # Verify the API key and get user/app data
        data = await verify_api_key(api_key)

        # Update request counts
        await update_request_count(data)

        return True

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error processing API key: {str(e)}"
        )
