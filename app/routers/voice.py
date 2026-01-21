"""Voice assistant endpoints - VAPI integration"""
from fastapi import APIRouter, HTTPException, Depends
from typing import Dict
import logging
import httpx

from app.models.voice import (
    VoiceAssistantSyncResponse,
    VoiceAssistantUpdate,
    VoiceConnectionValidation
)
from app.middleware.auth import get_current_user
from app.database import supabase_admin

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/sync", response_model=VoiceAssistantSyncResponse)
async def sync_voice_assistants(
    auth_data: Dict = Depends(get_current_user)
):
    """Sync voice assistants from VAPI"""
    try:
        user_id = auth_data["user_id"]

        # Get VAPI connection
        conn_result = supabase_admin.table("voice_connections").select(
            "*"
        ).eq("user_id", user_id).single().execute()

        if not conn_result.data:
            raise HTTPException(status_code=404, detail="VAPI connection not found")

        public_key = conn_result.data["public_key"]

        # Fetch assistants from VAPI
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.vapi.ai/assistant",
                headers={"Authorization": f"Bearer {public_key}"}
            )

            if response.status_code != 200:
                raise HTTPException(status_code=500, detail="Failed to sync from VAPI")

            assistants = response.json()

        # Upsert to database
        for assistant in assistants:
            supabase_admin.table("voice_assistants").upsert({
                "id": assistant["id"],
                "user_id": user_id,
                "name": assistant.get("name"),
                "first_message": assistant.get("firstMessage"),
                "voice_provider": assistant.get("voice", {}).get("provider"),
                "voice_id": assistant.get("voice", {}).get("voiceId"),
                "model_provider": assistant.get("model", {}).get("provider"),
                "model": assistant.get("model", {}).get("model"),
                "phone_number": assistant.get("phoneNumber"),
            }, on_conflict="id").execute()

        return VoiceAssistantSyncResponse(
            count=len(assistants),
            assistants=assistants
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Voice sync error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/{assistant_id}")
async def update_voice_assistant(
    assistant_id: str,
    updates: VoiceAssistantUpdate,
    auth_data: Dict = Depends(get_current_user)
):
    """Update voice assistant configuration"""
    try:
        user_id = auth_data["user_id"]

        # Verify ownership
        assistant = supabase_admin.table("voice_assistants").select("*").eq(
            "id", assistant_id
        ).eq("user_id", user_id).single().execute()

        if not assistant.data:
            raise HTTPException(status_code=404, detail="Assistant not found")

        # Get VAPI connection
        conn_result = supabase_admin.table("voice_connections").select(
            "public_key"
        ).eq("user_id", user_id).single().execute()

        public_key = conn_result.data["public_key"]

        # Update in VAPI
        update_data = updates.dict(exclude_unset=True)

        async with httpx.AsyncClient() as client:
            response = await client.patch(
                f"https://api.vapi.ai/assistant/{assistant_id}",
                headers={"Authorization": f"Bearer {public_key}"},
                json=update_data
            )

            if response.status_code != 200:
                raise HTTPException(status_code=500, detail="Failed to update in VAPI")

        # Update in database
        supabase_admin.table("voice_assistants").update(
            update_data
        ).eq("id", assistant_id).execute()

        return {"success": True, "message": "Assistant updated"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Voice update error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/validate")
async def validate_voice_connection(
    validation: VoiceConnectionValidation,
    auth_data: Dict = Depends(get_current_user)
):
    """Validate VAPI connection"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.vapi.ai/assistant",
                headers={"Authorization": f"Bearer {validation.public_key}"}
            )

            if response.status_code != 200:
                return {"valid": False, "error": "Invalid API key"}

        return {"valid": True}

    except Exception as e:
        return {"valid": False, "error": str(e)}


@router.get("/token")
async def get_vapi_web_token(auth_data: Dict = Depends(get_current_user)):
    """Get VAPI web token for frontend"""
    try:
        user_id = auth_data["user_id"]

        conn_result = supabase_admin.table("voice_connections").select(
            "public_key"
        ).eq("user_id", user_id).single().execute()

        if not conn_result.data:
            raise HTTPException(status_code=404, detail="Connection not found")

        return {"token": conn_result.data["public_key"]}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Token error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
