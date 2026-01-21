"""WhatsApp agent endpoints - ElevenLabs integration"""
from fastapi import APIRouter, HTTPException, Depends
from typing import Dict
import logging
import httpx

from app.models.whatsapp import WhatsAppAgentSyncResponse, WhatsAppAgentUpdate
from app.middleware.auth import get_current_user
from app.database import supabase_admin
from app.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter()
settings = get_settings()


@router.post("/sync", response_model=WhatsAppAgentSyncResponse)
async def sync_whatsapp_agents(auth_data: Dict = Depends(get_current_user)):
    """Sync WhatsApp agents from ElevenLabs"""
    try:
        user_id = auth_data["user_id"]

        conn_result = supabase_admin.table("elevenlabs_connections").select("*").eq(
            "user_id", user_id
        ).single().execute()

        if not conn_result.data:
            raise HTTPException(status_code=404, detail="ElevenLabs connection not found")

        api_key = conn_result.data["api_key"]

        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.elevenlabs.io/v1/convai/agents",
                headers={"xi-api-key": api_key}
            )

            if response.status_code != 200:
                raise HTTPException(status_code=500, detail="Failed to sync from ElevenLabs")

            agents = response.json()

        for agent in agents.get("agents", []):
            supabase_admin.table("whatsapp_agents").upsert({
                "id": agent["agent_id"],
                "user_id": user_id,
                "name": agent.get("name"),
                "status": "active"
            }, on_conflict="id").execute()

        return WhatsAppAgentSyncResponse(count=len(agents.get("agents", [])), agents=agents.get("agents", []))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"WhatsApp sync error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/{agent_id}")
async def update_whatsapp_agent(agent_id: str, updates: WhatsAppAgentUpdate, auth_data: Dict = Depends(get_current_user)):
    """Update WhatsApp agent"""
    try:
        user_id = auth_data["user_id"]

        agent = supabase_admin.table("whatsapp_agents").select("*").eq(
            "id", agent_id
        ).eq("user_id", user_id).single().execute()

        if not agent.data:
            raise HTTPException(status_code=404, detail="Agent not found")

        update_data = updates.dict(exclude_unset=True)
        supabase_admin.table("whatsapp_agents").update(update_data).eq("id", agent_id).execute()

        return {"success": True, "message": "Agent updated"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"WhatsApp update error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{agent_id}")
async def get_whatsapp_agent(agent_id: str, auth_data: Dict = Depends(get_current_user)):
    """Get WhatsApp agent details"""
    try:
        user_id = auth_data["user_id"]

        agent = supabase_admin.table("whatsapp_agents").select("*").eq(
            "id", agent_id
        ).eq("user_id", user_id).single().execute()

        if not agent.data:
            raise HTTPException(status_code=404, detail="Agent not found")

        return agent.data

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get agent error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/validate-elevenlabs")
async def validate_elevenlabs_connection(api_key: str, auth_data: Dict = Depends(get_current_user)):
    """Validate ElevenLabs API key connection"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.elevenlabs.io/v1/user",
                headers={"xi-api-key": api_key}
            )

            if response.status_code != 200:
                return {"valid": False, "error": "Invalid API key"}

            user_data = response.json()
            return {"valid": True, "user": user_data}

    except Exception as e:
        logger.error(f"ElevenLabs validation error: {e}")
        return {"valid": False, "error": str(e)}
