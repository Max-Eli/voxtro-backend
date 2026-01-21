"""OpenAI connection endpoints - For user API key management"""
from fastapi import APIRouter, HTTPException, Depends
from typing import Dict
from pydantic import BaseModel
import logging
import httpx

from app.middleware.auth import get_current_user
from app.database import supabase_admin

logger = logging.getLogger(__name__)
router = APIRouter()


class OpenAIConnectionCreate(BaseModel):
    api_key: str
    org_name: str | None = None


class OpenAIConnectionResponse(BaseModel):
    success: bool
    message: str
    connection_id: str | None = None


@router.post("/validate", response_model=Dict)
async def validate_openai_key(api_key: str):
    """Validate OpenAI API key by making a test request"""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}"}
            )

            if response.status_code == 200:
                return {"valid": True, "message": "API key is valid"}
            elif response.status_code == 401:
                return {"valid": False, "message": "Invalid API key"}
            else:
                return {"valid": False, "message": f"API error: {response.status_code}"}

    except Exception as e:
        logger.error(f"OpenAI validation error: {e}")
        return {"valid": False, "message": "Failed to validate API key"}


@router.post("", response_model=OpenAIConnectionResponse)
async def create_openai_connection(
    connection: OpenAIConnectionCreate,
    auth_data: Dict = Depends(get_current_user)
):
    """Save user's OpenAI API key"""
    try:
        user_id = auth_data["user_id"]

        # Validate the API key first
        validation = await validate_openai_key(connection.api_key)
        if not validation["valid"]:
            raise HTTPException(status_code=400, detail=validation["message"])

        # Deactivate any existing connections
        supabase_admin.table("openai_connections").update({
            "is_active": False
        }).eq("user_id", user_id).execute()

        # Create new connection
        result = supabase_admin.table("openai_connections").insert({
            "user_id": user_id,
            "api_key": connection.api_key,
            "org_name": connection.org_name,
            "is_active": True
        }).execute()

        return OpenAIConnectionResponse(
            success=True,
            message="OpenAI API key saved successfully",
            connection_id=result.data[0]["id"]
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"OpenAI connection error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("", response_model=Dict)
async def get_openai_connection(auth_data: Dict = Depends(get_current_user)):
    """Get user's active OpenAI connection (without exposing the full API key)"""
    try:
        user_id = auth_data["user_id"]

        result = supabase_admin.table("openai_connections").select(
            "id, org_name, is_active, created_at"
        ).eq("user_id", user_id).eq("is_active", True).single().execute()

        if result.data:
            return {
                "connected": True,
                "connection": result.data
            }
        else:
            return {"connected": False}

    except Exception as e:
        # No connection found is not an error
        return {"connected": False}


@router.delete("/{connection_id}")
async def delete_openai_connection(
    connection_id: str,
    auth_data: Dict = Depends(get_current_user)
):
    """Delete/deactivate OpenAI connection"""
    try:
        user_id = auth_data["user_id"]

        # Verify ownership and deactivate
        supabase_admin.table("openai_connections").update({
            "is_active": False
        }).eq("id", connection_id).eq("user_id", user_id).execute()

        return {"success": True, "message": "Connection removed"}

    except Exception as e:
        logger.error(f"Delete connection error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
