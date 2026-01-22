"""Authentication middleware and dependencies"""
from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional, Dict
from app.config import get_settings
import httpx
import logging

logger = logging.getLogger(__name__)

settings = get_settings()
security = HTTPBearer(auto_error=False)


async def verify_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> Optional[Dict]:
    """
    Verify Supabase JWT token via Supabase Auth API
    """
    if not credentials:
        return None

    token = credentials.credentials

    try:
        # Verify token by calling Supabase auth API
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{settings.supabase_url}/auth/v1/user",
                headers={
                    "Authorization": f"Bearer {token}",
                    "apikey": settings.supabase_anon_key
                }
            )

        if response.status_code != 200:
            logger.warning(f"Supabase auth failed: {response.status_code} - {response.text}")
            raise HTTPException(status_code=401, detail="Invalid authentication token")

        user_data = response.json()
        logger.info(f"Auth successful for user: {user_data.get('id')}")

        return {
            "user_id": user_data.get("id"),
            "role": user_data.get("role"),
            "email": user_data.get("email"),
            "is_customer": user_data.get("user_metadata", {}).get("is_customer", False),
            "raw_token": token,
            "metadata": user_data.get("user_metadata", {})
        }
    except httpx.RequestError:
        raise HTTPException(status_code=401, detail="Authentication service unavailable")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid authentication token")


async def get_current_user(
    auth_data: Optional[Dict] = Depends(verify_token)
) -> Dict:
    """
    Get current authenticated user (admin users only)

    Args:
        auth_data: Authentication data from verify_token

    Returns:
        User auth data

    Raises:
        HTTPException: If not authenticated or is customer
    """
    if not auth_data:
        raise HTTPException(status_code=401, detail="Not authenticated")

    if auth_data.get("is_customer"):
        raise HTTPException(
            status_code=403,
            detail="Customer access not allowed for this endpoint"
        )

    return auth_data


async def get_current_customer(
    auth_data: Optional[Dict] = Depends(verify_token)
) -> Dict:
    """
    Get current authenticated customer

    Args:
        auth_data: Authentication data from verify_token

    Returns:
        Customer auth data

    Raises:
        HTTPException: If not authenticated or not a customer
    """
    if not auth_data:
        raise HTTPException(status_code=401, detail="Not authenticated")

    if not auth_data.get("is_customer"):
        raise HTTPException(
            status_code=403,
            detail="This endpoint is only accessible to customers"
        )

    return auth_data


async def get_optional_user(
    auth_data: Optional[Dict] = Depends(verify_token)
) -> Optional[Dict]:
    """
    Get current user if authenticated, None otherwise (for public endpoints)

    Args:
        auth_data: Authentication data from verify_token

    Returns:
        User auth data or None
    """
    return auth_data


def apply_user_filter(query, user_id: str):
    """
    Apply user_id filter to query (equivalent to RLS)

    Args:
        query: Supabase query object
        user_id: User UUID

    Returns:
        Filtered query
    """
    return query.eq("user_id", user_id)
