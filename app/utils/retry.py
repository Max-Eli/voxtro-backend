"""
Retry utility for handling transient database connection errors.
This fixes the "Connection reset by peer" errors when fetching leads.
"""
import time
import logging
from typing import Callable, Any

logger = logging.getLogger(__name__)


def retry_supabase_query(query_func: Callable, max_retries: int = 3) -> Any:
    """
    Execute a Supabase query with retry logic for transient errors.

    Usage:
        result = retry_supabase_query(
            lambda: supabase_admin.table("customers").select("*").execute()
        )

    Args:
        query_func: A callable that executes the Supabase query
        max_retries: Maximum number of retry attempts

    Returns:
        The query result
    """
    last_exception = None
    base_delay = 0.5

    for attempt in range(max_retries + 1):
        try:
            return query_func()
        except (ConnectionResetError, ConnectionError, OSError) as e:
            last_exception = e
            error_msg = str(e)
            if "Connection reset by peer" in error_msg or "104" in error_msg:
                if attempt < max_retries:
                    delay = min(base_delay * (2 ** attempt), 4.0)
                    logger.warning(
                        f"Supabase connection reset, retry {attempt + 1}/{max_retries}. "
                        f"Waiting {delay}s..."
                    )
                    time.sleep(delay)
                    continue
            raise
        except Exception as e:
            error_str = str(e).lower()
            if "connection reset" in error_str or "errno 104" in error_str:
                last_exception = e
                if attempt < max_retries:
                    delay = min(base_delay * (2 ** attempt), 4.0)
                    logger.warning(
                        f"Supabase query failed with connection error, "
                        f"retry {attempt + 1}/{max_retries}. Waiting {delay}s..."
                    )
                    time.sleep(delay)
                    continue
            raise

    raise last_exception
