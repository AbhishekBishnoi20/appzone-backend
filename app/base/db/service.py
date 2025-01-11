from .connection import execute_query, single_fetch_query
from datetime import datetime, timezone

async def update_service_metrics(route: str, req_success: bool) -> None:
    """
    Update request counts and stability metrics for a given service route.

    Args:
        route (str): The route of the service to update
        req_success (bool): Whether the request was successful
    """
    current_time = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3] + 'Z'

    # Get current service data
    service_data = await single_fetch_query(
        "SELECT today_requests, today_stability FROM service WHERE route = ?",
        (route,)
    )

    if not service_data:
        # Create new service entry if it doesn't exist
        await execute_query("""
            INSERT INTO service (
                route, today_requests, requests,
                today_stability, stability,
                created, updated
            ) VALUES (?, 1, 1, ?, ?, ?, ?)
        """, (route, int(req_success), int(req_success), current_time, current_time))
        return

    # Update existing service metrics
    await execute_query("""
        UPDATE service
        SET today_requests = today_requests + 1,
            requests = requests + 1,
            today_stability = CAST((today_stability * today_requests + ?) / (today_requests + 1) AS INTEGER),
            stability = CAST((stability * requests + ?) / (requests + 1) AS INTEGER),
            updated = ?
        WHERE route = ?
    """, (int(req_success), int(req_success), current_time, route))
