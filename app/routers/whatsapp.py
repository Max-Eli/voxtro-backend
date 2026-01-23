"""WhatsApp agent endpoints - ElevenLabs integration"""
from fastapi import APIRouter, HTTPException, Depends, Request
from typing import Dict, Optional, Any
import logging
import httpx
import json
from datetime import datetime

from app.models.whatsapp import WhatsAppAgentSyncResponse, WhatsAppAgentUpdate
from app.middleware.auth import get_current_user
from app.database import supabase_admin
from app.config import get_settings
from app.services.ai_service import call_openai, get_user_openai_key

logger = logging.getLogger(__name__)
router = APIRouter()
settings = get_settings()


async def generate_whatsapp_summary(conversation_id: str, transcript_text: str, user_id: str) -> Optional[Dict[str, Any]]:
    """
    Generate AI summary of a WhatsApp conversation transcript

    Args:
        conversation_id: The conversation ID
        transcript_text: Full transcript text
        user_id: User ID to get OpenAI key

    Returns:
        Summary dict with summary, key_points, action_items, sentiment, lead_info
    """
    try:
        # Get user's OpenAI API key
        openai_api_key = await get_user_openai_key(user_id, allow_fallback=True)

        summary_prompt = f"""Analyze the following WhatsApp conversation transcript and provide a comprehensive summary.

TRANSCRIPT:
{transcript_text}

Provide your analysis in the following JSON format:
{{
    "summary": "Brief 2-3 sentence summary of the conversation",
    "key_points": ["Key point 1", "Key point 2", ...],
    "action_items": ["Action item 1", "Action item 2", ...],
    "sentiment": "positive/neutral/negative",
    "sentiment_notes": "Brief explanation of user sentiment",
    "lead_info": {{
        "name": "User name if mentioned",
        "email": "Email if mentioned",
        "phone": "Phone if mentioned",
        "company": "Company if mentioned",
        "interest_level": "high/medium/low/unknown",
        "notes": "Any relevant notes about the potential lead"
    }},
    "conversation_outcome": "resolved/follow_up_needed/escalated/information_provided/other",
    "topics_discussed": ["Topic 1", "Topic 2", ...]
}}

Respond ONLY with valid JSON, no additional text."""

        response = await call_openai(
            messages=[{"role": "user", "content": summary_prompt}],
            api_key=openai_api_key,
            model="gpt-4o-mini",
            temperature=0.3,
            max_tokens=1000
        )

        summary_data = json.loads(response["message"])
        return summary_data

    except Exception as e:
        logger.error(f"Error generating WhatsApp conversation summary: {e}")
        return None


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
    """Get WhatsApp agent details including ElevenLabs configuration"""
    try:
        user_id = auth_data["user_id"]

        # Verify agent belongs to user
        agent = supabase_admin.table("whatsapp_agents").select("*").eq(
            "id", agent_id
        ).eq("user_id", user_id).single().execute()

        if not agent.data:
            raise HTTPException(status_code=404, detail="Agent not found")

        # Get ElevenLabs connection
        conn_result = supabase_admin.table("elevenlabs_connections").select("*").eq(
            "user_id", user_id
        ).eq("is_active", True).single().execute()

        if not conn_result.data:
            raise HTTPException(status_code=404, detail="ElevenLabs connection not found")

        api_key = conn_result.data["api_key"]

        # Fetch agent configuration from ElevenLabs
        agent_config = None
        conversations = []

        async with httpx.AsyncClient() as client:
            # Get agent details from ElevenLabs
            agent_response = await client.get(
                f"https://api.elevenlabs.io/v1/convai/agents/{agent_id}",
                headers={"xi-api-key": api_key}
            )

            if agent_response.status_code == 200:
                agent_data = agent_response.json()
                agent_config = {
                    "agent_id": agent_id,
                    "name": agent_data.get("name", agent.data.get("name")),
                    "phone_number": agent.data.get("phone_number"),
                    "system_prompt": agent_data.get("conversation_config", {}).get("agent", {}).get("prompt", {}).get("prompt", ""),
                    "first_message": agent_data.get("conversation_config", {}).get("agent", {}).get("first_message", ""),
                    "language": agent_data.get("conversation_config", {}).get("agent", {}).get("language", "en"),
                    "voice_id": agent_data.get("conversation_config", {}).get("tts", {}).get("voice_id", ""),
                    "model_id": agent_data.get("conversation_config", {}).get("tts", {}).get("model_id", ""),
                    "llm_model": agent_data.get("conversation_config", {}).get("agent", {}).get("prompt", {}).get("llm", ""),
                    "temperature": agent_data.get("conversation_config", {}).get("agent", {}).get("prompt", {}).get("temperature", 0.7),
                    "max_tokens": agent_data.get("conversation_config", {}).get("agent", {}).get("prompt", {}).get("max_tokens", 1000),
                    "tools": agent_data.get("conversation_config", {}).get("agent", {}).get("prompt", {}).get("tools", []),
                    "data_collection": agent_data.get("conversation_config", {}).get("agent", {}).get("data_collection", {}),
                    "max_duration_seconds": agent_data.get("conversation_config", {}).get("conversation", {}).get("max_duration_seconds"),
                    "conversation_config": agent_data.get("conversation_config", {})
                }

            # Get conversations from ElevenLabs
            conv_response = await client.get(
                f"https://api.elevenlabs.io/v1/convai/conversations",
                headers={"xi-api-key": api_key},
                params={"agent_id": agent_id}
            )

            if conv_response.status_code == 200:
                conv_data = conv_response.json()
                conversations = conv_data.get("conversations", [])

        # If we couldn't get config from ElevenLabs, use database data
        if not agent_config:
            agent_config = {
                "agent_id": agent_id,
                "name": agent.data.get("name"),
                "phone_number": agent.data.get("phone_number"),
                "system_prompt": "",
                "first_message": "",
                "language": "en",
                "voice_id": "",
                "model_id": "",
                "llm_model": "",
                "temperature": 0.7,
                "max_tokens": 1000,
                "tools": [],
                "data_collection": {},
                "max_duration_seconds": None,
                "conversation_config": {}
            }

        return {
            "agent": agent_config,
            "conversations": conversations
        }

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


@router.post("/webhook/elevenlabs")
async def elevenlabs_webhook(request: Request):
    """
    Handle ElevenLabs webhook for WhatsApp conversation events
    This receives conversation data when a WhatsApp conversation ends
    """
    try:
        payload = await request.json()
        logger.info(f"ElevenLabs webhook received: {json.dumps(payload)[:500]}")

        event_type = payload.get("type") or payload.get("event_type")

        # Handle conversation end events
        if event_type in ["conversation.ended", "conversation_ended", "end_of_conversation"]:
            conversation_data = payload.get("conversation") or payload.get("data") or payload

            conversation_id = conversation_data.get("conversation_id") or conversation_data.get("id")
            agent_id = conversation_data.get("agent_id")

            if not conversation_id or not agent_id:
                logger.warning("Missing conversation_id or agent_id in webhook")
                return {"success": True, "message": "Missing required fields"}

            # Get agent to find user_id
            agent_result = supabase_admin.table("whatsapp_agents").select(
                "user_id"
            ).eq("id", agent_id).single().execute()

            if not agent_result.data:
                logger.warning(f"Agent not found: {agent_id}")
                return {"success": True, "message": "Agent not found"}

            user_id = agent_result.data["user_id"]

            # Extract phone number
            phone_number = (
                conversation_data.get("phone_number") or
                conversation_data.get("customer_phone") or
                conversation_data.get("from")
            )

            # Save conversation record
            conv_data = {
                "id": conversation_id,
                "agent_id": agent_id,
                "phone_number": phone_number,
                "status": conversation_data.get("status", "completed"),
                "started_at": conversation_data.get("started_at") or conversation_data.get("start_time") or datetime.utcnow().isoformat(),
                "ended_at": conversation_data.get("ended_at") or conversation_data.get("end_time") or datetime.utcnow().isoformat(),
            }

            supabase_admin.table("whatsapp_conversations").upsert(
                conv_data, on_conflict="id"
            ).execute()

            # Save messages/transcript
            transcript_text = ""
            messages = conversation_data.get("messages") or conversation_data.get("transcript", {}).get("messages", [])

            for msg in messages:
                role = msg.get("role", "unknown")
                content = msg.get("content") or msg.get("message") or msg.get("text", "")
                timestamp = msg.get("timestamp") or msg.get("time") or datetime.utcnow().isoformat()

                transcript_text += f"{role}: {content}\n"

                supabase_admin.table("whatsapp_messages").insert({
                    "conversation_id": conversation_id,
                    "role": role,
                    "content": content,
                    "timestamp": timestamp
                }).execute()

            # Generate AI summary if we have transcript
            if transcript_text.strip():
                summary = await generate_whatsapp_summary(conversation_id, transcript_text, user_id)

                if summary:
                    # Save summary to conversation record
                    supabase_admin.table("whatsapp_conversations").update({
                        "summary": summary.get("summary"),
                        "key_points": summary.get("key_points"),
                        "action_items": summary.get("action_items"),
                        "sentiment": summary.get("sentiment"),
                        "sentiment_notes": summary.get("sentiment_notes"),
                        "conversation_outcome": summary.get("conversation_outcome"),
                        "topics_discussed": summary.get("topics_discussed"),
                        "lead_info": summary.get("lead_info")
                    }).eq("id", conversation_id).execute()

                    logger.info(f"Generated summary for WhatsApp conversation {conversation_id}")

        return {"success": True}

    except Exception as e:
        logger.error(f"ElevenLabs webhook error: {e}")
        return {"success": False, "error": str(e)}


@router.post("/conversations/{conversation_id}/generate-summary")
async def regenerate_whatsapp_summary(
    conversation_id: str,
    auth_data: Dict = Depends(get_current_user)
):
    """
    Generate or regenerate AI summary for a WhatsApp conversation
    """
    try:
        user_id = auth_data["user_id"]

        # Verify conversation belongs to user's agent
        conv_result = supabase_admin.table("whatsapp_conversations").select(
            "id, agent_id"
        ).eq("id", conversation_id).single().execute()

        if not conv_result.data:
            raise HTTPException(status_code=404, detail="Conversation not found")

        # Verify agent belongs to user
        agent_result = supabase_admin.table("whatsapp_agents").select(
            "user_id"
        ).eq("id", conv_result.data["agent_id"]).single().execute()

        if not agent_result.data or agent_result.data["user_id"] != user_id:
            raise HTTPException(status_code=403, detail="Access denied")

        # Get messages to build transcript
        messages_result = supabase_admin.table("whatsapp_messages").select(
            "role, content"
        ).eq("conversation_id", conversation_id).order("timestamp", desc=False).execute()

        if not messages_result.data:
            raise HTTPException(status_code=400, detail="No messages found for conversation")

        # Build transcript text
        transcript_text = ""
        for msg in messages_result.data:
            transcript_text += f"{msg['role']}: {msg['content']}\n"

        # Generate summary
        summary = await generate_whatsapp_summary(conversation_id, transcript_text, user_id)

        if not summary:
            raise HTTPException(status_code=500, detail="Failed to generate summary")

        # Save summary
        supabase_admin.table("whatsapp_conversations").update({
            "summary": summary.get("summary"),
            "key_points": summary.get("key_points"),
            "action_items": summary.get("action_items"),
            "sentiment": summary.get("sentiment"),
            "sentiment_notes": summary.get("sentiment_notes"),
            "conversation_outcome": summary.get("conversation_outcome"),
            "topics_discussed": summary.get("topics_discussed"),
            "lead_info": summary.get("lead_info")
        }).eq("id", conversation_id).execute()

        return {
            "success": True,
            "summary": summary
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Regenerate WhatsApp summary error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
