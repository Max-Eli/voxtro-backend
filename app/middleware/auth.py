"""Authentication middleware and dependencies"""
from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from typing import Optional, Dict
from app.config import get_settings

settings = get_settings()
security = HTTPBearer(auto_error=False)


async def verify_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> Optional[Dict]:
    """
    Verify Supabase JWT token

    Args:
        credentials: HTTP Bearer credentials from request

    Returns:
        Dict containing user info if valid, None if no token provided

    Raises:
        HTTPException: If token is invalid
    """
    if not credentials:
        return None

    token = credentials.credentials

    try:
        # Decode JWT
        payload = jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            options={"verify_aud": False}
        )

        # Extract user info
        user_id = payload.get("sub")
        user_role = payload.get("role")
        email = payload.get("email")
        user_metadata = payload.get("user_metadata", {})
        is_customer = user_metadata.get("is_customer", False)

        return {
            "user_id": user_id,
            "role": user_role,
            "email": email,
            "is_customer": is_customer,
            "raw_token": token,
            "metadata": user_metadata
        }
    except JWTError as e:
        raise HTTPException(
            status_code=401,
            detail="Invalid authentication token"
        )


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
