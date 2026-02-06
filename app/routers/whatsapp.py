"""WhatsApp agent endpoints - ElevenLabs integration"""
from fastapi import APIRouter, HTTPException, Depends, Request
from typing import Dict, Optional, Any
import logging
import httpx
import json
import uuid
from datetime import datetime

from app.models.whatsapp import WhatsAppAgentSyncResponse, WhatsAppAgentUpdate, ElevenLabsConnectionValidation
from app.middleware.auth import get_current_user
from app.database import supabase_admin
from app.config import get_settings
from app.services.ai_service import call_mistral

logger = logging.getLogger(__name__)
router = APIRouter()
settings = get_settings()


async def generate_whatsapp_summary(conversation_id: str, transcript_text: str, user_id: str) -> Optional[Dict[str, Any]]:
    """
    Generate AI summary of a WhatsApp conversation transcript using Mistral

    Args:
        conversation_id: The conversation ID
        transcript_text: Full transcript text
        user_id: User ID (kept for interface compatibility)

    Returns:
        Summary dict with summary, key_points, action_items, sentiment, lead_info
    """
    try:
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

        response = await call_mistral(
            messages=[{"role": "user", "content": summary_prompt}],
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

        # Get the agent first (without user_id filter to allow team access)
        agent = supabase_admin.table("whatsapp_agents").select("*").eq(
            "id", agent_id
        ).single().execute()

        if not agent.data:
            raise HTTPException(status_code=404, detail="Agent not found")

        agent_owner_id = agent.data["user_id"]

        # Verify user has access: either owner or teammate
        if agent_owner_id != user_id:
            # Check if user is a teammate of the agent owner
            teammate_check = supabase_admin.rpc(
                "get_direct_teammates",
                {"user_uuid": user_id}
            ).execute()

            teammate_ids = [t for t in (teammate_check.data or [])]
            if agent_owner_id not in teammate_ids:
                raise HTTPException(status_code=403, detail="Access denied - not owner or teammate")

        # Get the agent OWNER's ElevenLabs connection (not the current user's)
        conn_result = supabase_admin.table("elevenlabs_connections").select("*").eq(
            "user_id", agent_owner_id
        ).eq("is_active", True).single().execute()

        if not conn_result.data:
            raise HTTPException(status_code=404, detail="ElevenLabs connection not found for agent owner")

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
async def validate_elevenlabs_connection(
    validation: ElevenLabsConnectionValidation = None,
    api_key: str = None,
    auth_data: Dict = Depends(get_current_user)
):
    """Validate ElevenLabs API key connection - accepts key via body or query param"""
    try:
        # Support both body (validation.api_key) and query param (api_key)
        key = None
        if validation and validation.api_key:
            key = validation.api_key.strip()
        elif api_key:
            key = api_key.strip()

        if not key:
            return {"valid": False, "error": "API key is required"}

        logger.info(f"Validating ElevenLabs API key (length: {len(key)})")

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                "https://api.elevenlabs.io/v1/user",
                headers={"xi-api-key": key}
            )

            logger.info(f"ElevenLabs validation response: {response.status_code}")

            if response.status_code == 401:
                return {"valid": False, "error": "Invalid API key - unauthorized"}
            elif response.status_code == 403:
                return {"valid": False, "error": "API key does not have required permissions"}
            elif response.status_code != 200:
                error_text = response.text[:200] if response.text else "Unknown error"
                logger.error(f"ElevenLabs API error: {response.status_code} - {error_text}")
                return {"valid": False, "error": f"API error: {response.status_code}"}

            user_data = response.json()
            return {"valid": True, "user": user_data}

    except httpx.TimeoutException:
        logger.error("ElevenLabs validation timeout")
        return {"valid": False, "error": "Connection timeout - please try again"}
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


@router.post("/{agent_id}/fetch-conversations")
async def fetch_whatsapp_conversations(
    agent_id: str,
    auth_data: Dict = Depends(get_current_user)
):
    """
    Fetch conversations from ElevenLabs for a WhatsApp agent and sync to database.
    This fetches the list of conversations and their transcripts.
    """
    try:
        user_id = auth_data["user_id"]

        # Verify agent belongs to user
        agent_result = supabase_admin.table("whatsapp_agents").select(
            "id, user_id"
        ).eq("id", agent_id).eq("user_id", user_id).single().execute()

        if not agent_result.data:
            raise HTTPException(status_code=404, detail="Agent not found")

        # Get ElevenLabs connection
        conn_result = supabase_admin.table("elevenlabs_connections").select(
            "api_key"
        ).eq("user_id", user_id).eq("is_active", True).single().execute()

        if not conn_result.data:
            raise HTTPException(status_code=404, detail="ElevenLabs connection not found")

        api_key = conn_result.data["api_key"]

        synced_count = 0
        new_conversations = 0
        summaries_generated = 0

        async with httpx.AsyncClient(timeout=60.0) as client:
            # Fetch conversations list from ElevenLabs
            conv_list_response = await client.get(
                "https://api.elevenlabs.io/v1/convai/conversations",
                headers={"xi-api-key": api_key},
                params={"agent_id": agent_id}
            )

            if conv_list_response.status_code != 200:
                logger.error(f"Failed to fetch conversations: {conv_list_response.status_code} - {conv_list_response.text[:200]}")
                raise HTTPException(status_code=500, detail="Failed to fetch conversations from ElevenLabs")

            conv_list = conv_list_response.json()
            conversations = conv_list.get("conversations", [])

            logger.info(f"Found {len(conversations)} conversations for agent {agent_id}")

            for conv in conversations:
                conversation_id = conv.get("conversation_id")
                if not conversation_id:
                    continue

                synced_count += 1

                # Check if conversation already exists in database
                existing = supabase_admin.table("whatsapp_conversations").select(
                    "id, summary"
                ).eq("id", conversation_id).execute()

                # Fetch individual conversation details to get transcript
                conv_detail_response = await client.get(
                    f"https://api.elevenlabs.io/v1/convai/conversations/{conversation_id}",
                    headers={"xi-api-key": api_key}
                )

                if conv_detail_response.status_code != 200:
                    logger.warning(f"Failed to fetch conversation {conversation_id}: {conv_detail_response.status_code}")
                    continue

                conv_detail = conv_detail_response.json()

                # Extract conversation data
                status = conv_detail.get("status", "done")
                metadata = conv_detail.get("metadata", {})
                transcript = conv_detail.get("transcript", [])
                analysis = conv_detail.get("analysis", {})

                # Get timestamps - ElevenLabs uses start_time_unix_secs (Unix timestamp in seconds)
                start_unix = metadata.get("start_time_unix_secs") or conv.get("start_time_unix_secs")
                call_duration = metadata.get("call_duration_secs") or conv.get("call_duration_secs") or 0

                # Convert Unix timestamp to ISO format
                started_at = None
                ended_at = None
                if start_unix:
                    started_at = datetime.utcfromtimestamp(start_unix).isoformat() + "Z"
                    if call_duration:
                        ended_at = datetime.utcfromtimestamp(start_unix + call_duration).isoformat() + "Z"
                else:
                    # Fallback to current time if no timestamp available
                    started_at = datetime.utcnow().isoformat() + "Z"

                # Get phone number from various possible locations per ElevenLabs API
                phone_number = None

                # Check conversation_initiation_client_data for dynamic variables
                client_data = conv_detail.get("conversation_initiation_client_data", {})
                dynamic_vars = client_data.get("dynamic_variables", {})
                if dynamic_vars:
                    phone_number = dynamic_vars.get("system__caller_id") or dynamic_vars.get("system__called_number")

                # Check metadata.phone_call for phone call metadata
                if not phone_number and metadata.get("phone_call"):
                    phone_call = metadata["phone_call"]
                    phone_number = phone_call.get("from_number") or phone_call.get("to_number")

                # Check metadata.whatsapp for WhatsApp metadata
                if not phone_number and metadata.get("whatsapp"):
                    whatsapp = metadata["whatsapp"]
                    phone_number = whatsapp.get("phone_number") or whatsapp.get("from") or whatsapp.get("user_id")

                # Upsert conversation record
                conv_data = {
                    "id": conversation_id,
                    "agent_id": agent_id,
                    "phone_number": phone_number,
                    "status": status,
                    "started_at": started_at,
                    "ended_at": ended_at
                }

                # Add analysis data if available from ElevenLabs
                if analysis:
                    if analysis.get("transcript_summary"):
                        conv_data["summary"] = analysis.get("transcript_summary")
                    if analysis.get("evaluation_criteria_results"):
                        conv_data["evaluation_results"] = analysis.get("evaluation_criteria_results")
                    if analysis.get("data_collection_results"):
                        conv_data["data_collection"] = analysis.get("data_collection_results")

                supabase_admin.table("whatsapp_conversations").upsert(
                    conv_data, on_conflict="id"
                ).execute()

                if not existing.data:
                    new_conversations += 1

                # Process transcript messages
                if transcript:
                    # Check if we already have messages for this conversation
                    existing_messages = supabase_admin.table("whatsapp_messages").select(
                        "id"
                    ).eq("conversation_id", conversation_id).limit(1).execute()

                    if not existing_messages.data:
                        # Insert all transcript messages
                        transcript_text = ""
                        messages_to_insert = []

                        for msg in transcript:
                            # ElevenLabs transcript format
                            role = msg.get("role", "unknown")
                            # Map ElevenLabs roles to standard roles
                            if role == "agent":
                                role = "assistant"
                            elif role == "user":
                                role = "user"

                            content = msg.get("message", "") or msg.get("text", "") or msg.get("content", "")
                            timestamp = msg.get("time_in_call_secs") or msg.get("timestamp")

                            # Convert time_in_call_secs to timestamp if needed
                            if isinstance(timestamp, (int, float)) and started_at:
                                try:
                                    from datetime import timedelta
                                    start_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                                    msg_dt = start_dt + timedelta(seconds=timestamp)
                                    timestamp = msg_dt.isoformat()
                                except:
                                    timestamp = datetime.utcnow().isoformat()
                            elif not timestamp:
                                timestamp = datetime.utcnow().isoformat()

                            if content:
                                transcript_text += f"{role}: {content}\n"
                                messages_to_insert.append({
                                    "id": str(uuid.uuid4()),
                                    "conversation_id": conversation_id,
                                    "role": role,
                                    "content": content,
                                    "timestamp": timestamp if isinstance(timestamp, str) else datetime.utcnow().isoformat()
                                })

                        # Batch insert messages
                        if messages_to_insert:
                            supabase_admin.table("whatsapp_messages").insert(
                                messages_to_insert
                            ).execute()
                            logger.info(f"Inserted {len(messages_to_insert)} messages for conversation {conversation_id}")

                        # Generate AI summary if we have transcript and no existing summary
                        conv_record = supabase_admin.table("whatsapp_conversations").select(
                            "summary"
                        ).eq("id", conversation_id).single().execute()

                        if transcript_text.strip() and (not conv_record.data or not conv_record.data.get("summary")):
                            summary = await generate_whatsapp_summary(conversation_id, transcript_text, user_id)

                            if summary:
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

                                summaries_generated += 1
                                logger.info(f"Generated summary for conversation {conversation_id}")

                                # Extract lead to leads table if we have contact info
                                lead_info = summary.get("lead_info", {})
                                if lead_info and (lead_info.get("name") or lead_info.get("email") or lead_info.get("phone")):
                                    # Check if lead already exists for this conversation
                                    existing_lead = supabase_admin.table("leads").select(
                                        "id"
                                    ).eq("conversation_id", conversation_id).limit(1).execute()

                                    if not existing_lead.data:
                                        # Get agent name for source_name
                                        agent_info = supabase_admin.table("whatsapp_agents").select(
                                            "name"
                                        ).eq("id", agent_id).single().execute()
                                        agent_name = agent_info.data.get("name") if agent_info.data else None

                                        supabase_admin.table("leads").insert({
                                            "conversation_id": conversation_id,
                                            "source_id": agent_id,
                                            "source_type": "whatsapp",
                                            "source_name": agent_name,
                                            "user_id": user_id,
                                            "name": lead_info.get("name"),
                                            "email": lead_info.get("email"),
                                            "phone_number": lead_info.get("phone") or phone_number,
                                            "additional_data": {
                                                "company": lead_info.get("company"),
                                                "interest_level": lead_info.get("interest_level"),
                                                "notes": lead_info.get("notes")
                                            }
                                        }).execute()
                                        logger.info(f"Extracted lead from WhatsApp conversation {conversation_id}")

        return {
            "success": True,
            "synced": synced_count,
            "new_conversations": new_conversations,
            "summaries_generated": summaries_generated,
            "message": f"Synced {synced_count} conversations, {new_conversations} new, {summaries_generated} summaries generated"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Fetch WhatsApp conversations error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
