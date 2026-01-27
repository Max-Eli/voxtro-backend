"""Admin endpoints for manual sync operations and scheduled background tasks"""
from fastapi import APIRouter, HTTPException, Depends, Query
from typing import Dict, Optional
import logging
import uuid
import httpx
from datetime import datetime
import asyncio

from app.middleware.auth import get_current_user
from app.database import supabase_admin

logger = logging.getLogger(__name__)
router = APIRouter()


# ==================== Background Sync Functions (No Auth) ====================
# These functions are called by the scheduler, not by HTTP requests

async def background_sync_all_whatsapp():
    """Background task to sync all WhatsApp conversations for all users"""
    logger.info("Starting scheduled WhatsApp sync...")

    try:
        # Get all active ElevenLabs connections
        connections = supabase_admin.table("elevenlabs_connections").select(
            "user_id, api_key"
        ).eq("is_active", True).execute()

        if not connections.data:
            logger.info("No active ElevenLabs connections found")
            return

        total_synced = 0
        total_summaries = 0

        for conn in connections.data:
            owner_user_id = conn["user_id"]
            api_key = conn["api_key"]

            try:
                # Get all WhatsApp agents for this user
                agents = supabase_admin.table("whatsapp_agents").select(
                    "id, name"
                ).eq("user_id", owner_user_id).execute()

                if not agents.data:
                    continue

                async with httpx.AsyncClient(timeout=60.0) as client:
                    for agent in agents.data:
                        agent_id = agent["id"]

                        try:
                            # Fetch conversations from ElevenLabs
                            conv_list_response = await client.get(
                                "https://api.elevenlabs.io/v1/convai/conversations",
                                headers={"xi-api-key": api_key},
                                params={"agent_id": agent_id}
                            )

                            if conv_list_response.status_code != 200:
                                continue

                            conversations = conv_list_response.json().get("conversations", [])

                            for conv in conversations:
                                conversation_id = conv.get("conversation_id")
                                if not conversation_id:
                                    continue

                                # Check if conversation exists and has summary
                                existing = supabase_admin.table("whatsapp_conversations").select(
                                    "id, summary"
                                ).eq("id", conversation_id).execute()

                                is_new = not existing.data
                                needs_summary = is_new or not existing.data[0].get("summary") if existing.data else True

                                # Fetch conversation details
                                conv_detail_response = await client.get(
                                    f"https://api.elevenlabs.io/v1/convai/conversations/{conversation_id}",
                                    headers={"xi-api-key": api_key}
                                )

                                if conv_detail_response.status_code != 200:
                                    continue

                                conv_detail = conv_detail_response.json()
                                metadata = conv_detail.get("metadata", {})
                                transcript = conv_detail.get("transcript", [])
                                analysis = conv_detail.get("analysis", {})

                                # Get timestamps
                                start_unix = metadata.get("start_time_unix_secs") or conv.get("start_time_unix_secs")
                                call_duration = metadata.get("call_duration_secs") or conv.get("call_duration_secs") or 0

                                started_at = None
                                ended_at = None
                                if start_unix:
                                    started_at = datetime.utcfromtimestamp(start_unix).isoformat() + "Z"
                                    if call_duration:
                                        ended_at = datetime.utcfromtimestamp(start_unix + call_duration).isoformat() + "Z"
                                else:
                                    started_at = datetime.utcnow().isoformat() + "Z"

                                # Get phone number
                                phone_number = None
                                client_data = conv_detail.get("conversation_initiation_client_data", {})
                                dynamic_vars = client_data.get("dynamic_variables", {})
                                if dynamic_vars:
                                    phone_number = dynamic_vars.get("system__caller_id") or dynamic_vars.get("system__called_number")
                                if not phone_number and metadata.get("phone_call"):
                                    phone_call = metadata["phone_call"]
                                    phone_number = phone_call.get("from_number") or phone_call.get("to_number")
                                if not phone_number and metadata.get("whatsapp"):
                                    whatsapp = metadata["whatsapp"]
                                    phone_number = whatsapp.get("phone_number") or whatsapp.get("from") or whatsapp.get("user_id")

                                # Upsert conversation
                                conv_data = {
                                    "id": conversation_id,
                                    "agent_id": agent_id,
                                    "phone_number": phone_number,
                                    "status": conv_detail.get("status", "done"),
                                    "started_at": started_at,
                                    "ended_at": ended_at
                                }

                                if analysis and analysis.get("transcript_summary"):
                                    conv_data["summary"] = analysis.get("transcript_summary")

                                supabase_admin.table("whatsapp_conversations").upsert(
                                    conv_data, on_conflict="id"
                                ).execute()

                                # Insert transcript messages
                                transcript_text = ""
                                if transcript:
                                    existing_msgs = supabase_admin.table("whatsapp_messages").select(
                                        "id, role, content"
                                    ).eq("conversation_id", conversation_id).execute()

                                    if not existing_msgs.data:
                                        messages_to_insert = []
                                        for msg in transcript:
                                            role = msg.get("role", "unknown")
                                            if role == "agent":
                                                role = "assistant"
                                            content = msg.get("message", "") or msg.get("text", "")
                                            if content:
                                                messages_to_insert.append({
                                                    "id": str(uuid.uuid4()),
                                                    "conversation_id": conversation_id,
                                                    "role": role,
                                                    "content": content,
                                                    "timestamp": datetime.utcnow().isoformat()
                                                })

                                        if messages_to_insert:
                                            supabase_admin.table("whatsapp_messages").insert(
                                                messages_to_insert
                                            ).execute()
                                            transcript_text = "\n".join([f"{m['role']}: {m['content']}" for m in messages_to_insert])
                                    else:
                                        transcript_text = "\n".join([f"{m['role']}: {m['content']}" for m in existing_msgs.data])

                                # Generate AI summary if needed
                                if needs_summary and transcript_text.strip():
                                    try:
                                        from app.routers.whatsapp import generate_whatsapp_summary
                                        summary = await generate_whatsapp_summary(conversation_id, transcript_text, owner_user_id)

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

                                            # Extract lead
                                            lead_info = summary.get("lead_info", {})
                                            if lead_info and (lead_info.get("name") or lead_info.get("email") or lead_info.get("phone")):
                                                existing_lead = supabase_admin.table("leads").select(
                                                    "id"
                                                ).eq("conversation_id", conversation_id).limit(1).execute()

                                                if not existing_lead.data:
                                                    supabase_admin.table("leads").insert({
                                                        "conversation_id": conversation_id,
                                                        "source_id": agent_id,
                                                        "source_type": "whatsapp",
                                                        "source_name": agent["name"],
                                                        "user_id": owner_user_id,
                                                        "name": lead_info.get("name"),
                                                        "email": lead_info.get("email"),
                                                        "phone_number": lead_info.get("phone") or phone_number,
                                                        "additional_data": {
                                                            "company": lead_info.get("company"),
                                                            "interest_level": lead_info.get("interest_level"),
                                                            "notes": lead_info.get("notes")
                                                        }
                                                    }).execute()

                                            total_summaries += 1
                                    except Exception as ai_error:
                                        logger.warning(f"AI summary error for {conversation_id}: {ai_error}")

                                total_synced += 1

                        except Exception as agent_error:
                            logger.warning(f"Error syncing agent {agent_id}: {agent_error}")
                            continue

            except Exception as user_error:
                logger.warning(f"Error syncing user {owner_user_id}: {user_error}")
                continue

        logger.info(f"WhatsApp sync complete: {total_synced} conversations, {total_summaries} summaries generated")

    except Exception as e:
        logger.error(f"Background WhatsApp sync error: {e}")


async def background_sync_all_voice():
    """Background task to sync all Voice Assistant calls for all users"""
    logger.info("Starting scheduled Voice sync...")

    try:
        # Get all active VAPI connections
        connections = supabase_admin.table("vapi_connections").select(
            "user_id, api_key"
        ).eq("is_active", True).execute()

        if not connections.data:
            logger.info("No active VAPI connections found")
            return

        total_synced = 0
        total_summaries = 0

        for conn in connections.data:
            owner_user_id = conn["user_id"]
            api_key = conn["api_key"]

            try:
                # Get all voice assistants for this user
                assistants = supabase_admin.table("voice_assistants").select(
                    "id, name"
                ).eq("user_id", owner_user_id).execute()

                if not assistants.data:
                    continue

                async with httpx.AsyncClient(timeout=60.0) as client:
                    for assistant in assistants.data:
                        assistant_id = assistant["id"]

                        try:
                            # Fetch calls from VAPI
                            calls_response = await client.get(
                                "https://api.vapi.ai/call",
                                headers={"Authorization": f"Bearer {api_key}"},
                                params={"assistantId": assistant_id, "limit": 100}
                            )

                            if calls_response.status_code != 200:
                                continue

                            calls = calls_response.json()
                            if isinstance(calls, dict):
                                calls = calls.get("calls", []) or calls.get("data", [])

                            for call in calls:
                                call_id = call.get("id")
                                if not call_id:
                                    continue

                                # Check if call exists
                                existing = supabase_admin.table("voice_assistant_calls").select(
                                    "id, summary"
                                ).eq("id", call_id).execute()

                                is_new = not existing.data
                                needs_summary = is_new or not existing.data[0].get("summary") if existing.data else True

                                # Parse call data
                                started_at = call.get("startedAt") or call.get("createdAt")
                                ended_at = call.get("endedAt")
                                duration = None
                                if started_at and ended_at:
                                    try:
                                        start_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                                        end_dt = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
                                        duration = int((end_dt - start_dt).total_seconds())
                                    except:
                                        pass

                                phone_number = call.get("customer", {}).get("number") if call.get("customer") else None

                                # Upsert call
                                call_data = {
                                    "id": call_id,
                                    "assistant_id": assistant_id,
                                    "phone_number": phone_number,
                                    "status": call.get("status", "ended"),
                                    "started_at": started_at,
                                    "ended_at": ended_at,
                                    "duration_seconds": duration,
                                    "recording_url": call.get("recordingUrl"),
                                    "cost": call.get("cost")
                                }

                                supabase_admin.table("voice_assistant_calls").upsert(
                                    call_data, on_conflict="id"
                                ).execute()

                                # Get transcript
                                transcript_text = ""
                                transcript = call.get("transcript") or call.get("messages", [])

                                if transcript:
                                    if isinstance(transcript, str):
                                        transcript_text = transcript
                                    elif isinstance(transcript, list):
                                        existing_transcripts = supabase_admin.table("voice_call_transcripts").select(
                                            "id"
                                        ).eq("call_id", call_id).execute()

                                        if not existing_transcripts.data:
                                            for msg in transcript:
                                                role = msg.get("role", "unknown")
                                                content = msg.get("content") or msg.get("message") or msg.get("text", "")
                                                if content:
                                                    supabase_admin.table("voice_call_transcripts").insert({
                                                        "call_id": call_id,
                                                        "role": role,
                                                        "content": content,
                                                        "timestamp": msg.get("timestamp") or datetime.utcnow().isoformat()
                                                    }).execute()

                                        transcript_text = "\n".join([
                                            f"{m.get('role', 'unknown')}: {m.get('content') or m.get('message') or m.get('text', '')}"
                                            for m in transcript if m.get('content') or m.get('message') or m.get('text')
                                        ])

                                # Generate AI summary if needed
                                if needs_summary and transcript_text.strip():
                                    try:
                                        from app.routers.voice import generate_call_summary
                                        summary = await generate_call_summary(call_id, transcript_text, owner_user_id)

                                        if summary:
                                            supabase_admin.table("voice_assistant_calls").update({
                                                "summary": summary.get("summary"),
                                                "key_points": summary.get("key_points"),
                                                "action_items": summary.get("action_items"),
                                                "sentiment": summary.get("sentiment"),
                                                "sentiment_notes": summary.get("sentiment_notes"),
                                                "conversation_outcome": summary.get("conversation_outcome"),
                                                "topics_discussed": summary.get("topics_discussed"),
                                                "lead_info": summary.get("lead_info")
                                            }).eq("id", call_id).execute()

                                            # Extract lead
                                            lead_info = summary.get("lead_info", {})
                                            if lead_info and (lead_info.get("name") or lead_info.get("email") or lead_info.get("phone")):
                                                existing_lead = supabase_admin.table("leads").select(
                                                    "id"
                                                ).eq("conversation_id", call_id).limit(1).execute()

                                                if not existing_lead.data:
                                                    supabase_admin.table("leads").insert({
                                                        "conversation_id": call_id,
                                                        "source_id": assistant_id,
                                                        "source_type": "voice",
                                                        "source_name": assistant["name"],
                                                        "user_id": owner_user_id,
                                                        "name": lead_info.get("name"),
                                                        "email": lead_info.get("email"),
                                                        "phone_number": lead_info.get("phone") or phone_number,
                                                        "additional_data": {
                                                            "company": lead_info.get("company"),
                                                            "interest_level": lead_info.get("interest_level"),
                                                            "notes": lead_info.get("notes")
                                                        }
                                                    }).execute()

                                            total_summaries += 1
                                    except Exception as ai_error:
                                        logger.warning(f"AI summary error for call {call_id}: {ai_error}")

                                total_synced += 1

                        except Exception as assistant_error:
                            logger.warning(f"Error syncing assistant {assistant_id}: {assistant_error}")
                            continue

            except Exception as user_error:
                logger.warning(f"Error syncing user {owner_user_id}: {user_error}")
                continue

        logger.info(f"Voice sync complete: {total_synced} calls, {total_summaries} summaries generated")

    except Exception as e:
        logger.error(f"Background Voice sync error: {e}")


async def background_sync_all_chatbots():
    """Background task to generate AI summaries for chatbot conversations without summaries"""
    logger.info("Starting scheduled Chatbot summary generation...")

    try:
        # Get conversations without summaries that have messages
        conversations = supabase_admin.table("conversations").select(
            "id, chatbot_id, visitor_id"
        ).is_("summary", "null").limit(50).execute()  # Process 50 at a time

        if not conversations.data:
            logger.info("No chatbot conversations need summaries")
            return

        total_processed = 0
        total_summaries = 0

        for conv in conversations.data:
            conversation_id = conv["id"]
            chatbot_id = conv["chatbot_id"]

            try:
                # Get chatbot owner
                chatbot = supabase_admin.table("chatbots").select(
                    "user_id, name"
                ).eq("id", chatbot_id).single().execute()

                if not chatbot.data:
                    continue

                owner_user_id = chatbot.data["user_id"]

                # Check if conversation has messages
                messages = supabase_admin.table("messages").select(
                    "role, content"
                ).eq("conversation_id", conversation_id).execute()

                if not messages.data or len(messages.data) < 2:
                    continue

                # Generate summary
                from app.routers.chat import generate_conversation_summary
                summary = await generate_conversation_summary(conversation_id, owner_user_id)

                if summary:
                    supabase_admin.table("conversations").update({
                        "summary": summary.get("summary"),
                        "key_points": summary.get("key_points"),
                        "action_items": summary.get("action_items"),
                        "sentiment": summary.get("sentiment"),
                        "sentiment_notes": summary.get("sentiment_notes"),
                        "conversation_outcome": summary.get("conversation_outcome"),
                        "topics_discussed": summary.get("topics_discussed"),
                        "lead_info": summary.get("lead_info")
                    }).eq("id", conversation_id).execute()

                    # Extract lead
                    lead_info = summary.get("lead_info", {})
                    if lead_info and (lead_info.get("name") or lead_info.get("email") or lead_info.get("phone")):
                        existing_lead = supabase_admin.table("leads").select(
                            "id"
                        ).eq("conversation_id", conversation_id).limit(1).execute()

                        if not existing_lead.data:
                            supabase_admin.table("leads").insert({
                                "conversation_id": conversation_id,
                                "source_id": chatbot_id,
                                "source_type": "chatbot",
                                "source_name": chatbot.data["name"],
                                "user_id": owner_user_id,
                                "name": lead_info.get("name"),
                                "email": lead_info.get("email"),
                                "phone_number": lead_info.get("phone"),
                                "additional_data": {
                                    "company": lead_info.get("company"),
                                    "interest_level": lead_info.get("interest_level"),
                                    "notes": lead_info.get("notes")
                                }
                            }).execute()

                    total_summaries += 1

                total_processed += 1

            except Exception as conv_error:
                logger.warning(f"Error processing conversation {conversation_id}: {conv_error}")
                continue

        logger.info(f"Chatbot sync complete: {total_processed} processed, {total_summaries} summaries generated")

    except Exception as e:
        logger.error(f"Background Chatbot sync error: {e}")


def run_scheduled_sync():
    """Wrapper to run async sync functions from the scheduler"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(background_sync_all_whatsapp())
        loop.run_until_complete(background_sync_all_voice())
        loop.run_until_complete(background_sync_all_chatbots())
    finally:
        loop.close()


# ==================== HTTP Endpoints (With Auth) ====================


@router.post("/sync-all-whatsapp")
async def sync_all_whatsapp_conversations(
    auth_data: Dict = Depends(get_current_user),
    generate_summaries: bool = Query(True, description="Generate AI summaries for conversations without them")
):
    """
    Admin endpoint to sync WhatsApp conversations for ALL users.
    This fetches conversations from ElevenLabs and generates AI summaries.
    """
    try:
        user_id = auth_data["user_id"]

        # Get all active ElevenLabs connections
        connections = supabase_admin.table("elevenlabs_connections").select(
            "user_id, api_key"
        ).eq("is_active", True).execute()

        if not connections.data:
            return {"success": True, "message": "No active ElevenLabs connections found", "total_synced": 0}

        total_synced = 0
        total_summaries = 0
        errors = []

        for conn in connections.data:
            owner_user_id = conn["user_id"]
            api_key = conn["api_key"]

            try:
                # Get all WhatsApp agents for this user
                agents = supabase_admin.table("whatsapp_agents").select(
                    "id, name"
                ).eq("user_id", owner_user_id).execute()

                if not agents.data:
                    continue

                async with httpx.AsyncClient(timeout=60.0) as client:
                    for agent in agents.data:
                        agent_id = agent["id"]

                        try:
                            # Fetch conversations from ElevenLabs
                            conv_list_response = await client.get(
                                "https://api.elevenlabs.io/v1/convai/conversations",
                                headers={"xi-api-key": api_key},
                                params={"agent_id": agent_id}
                            )

                            if conv_list_response.status_code != 200:
                                continue

                            conversations = conv_list_response.json().get("conversations", [])

                            for conv in conversations:
                                conversation_id = conv.get("conversation_id")
                                if not conversation_id:
                                    continue

                                # Check if conversation exists and has summary
                                existing = supabase_admin.table("whatsapp_conversations").select(
                                    "id, summary"
                                ).eq("id", conversation_id).execute()

                                is_new = not existing.data
                                needs_summary = generate_summaries and (is_new or not existing.data[0].get("summary") if existing.data else True)

                                # Fetch conversation details
                                conv_detail_response = await client.get(
                                    f"https://api.elevenlabs.io/v1/convai/conversations/{conversation_id}",
                                    headers={"xi-api-key": api_key}
                                )

                                if conv_detail_response.status_code != 200:
                                    continue

                                conv_detail = conv_detail_response.json()
                                metadata = conv_detail.get("metadata", {})
                                transcript = conv_detail.get("transcript", [])
                                analysis = conv_detail.get("analysis", {})

                                # Get timestamps
                                start_unix = metadata.get("start_time_unix_secs") or conv.get("start_time_unix_secs")
                                call_duration = metadata.get("call_duration_secs") or conv.get("call_duration_secs") or 0

                                started_at = None
                                ended_at = None
                                if start_unix:
                                    started_at = datetime.utcfromtimestamp(start_unix).isoformat() + "Z"
                                    if call_duration:
                                        ended_at = datetime.utcfromtimestamp(start_unix + call_duration).isoformat() + "Z"
                                else:
                                    started_at = datetime.utcnow().isoformat() + "Z"

                                # Get phone number
                                phone_number = None
                                client_data = conv_detail.get("conversation_initiation_client_data", {})
                                dynamic_vars = client_data.get("dynamic_variables", {})
                                if dynamic_vars:
                                    phone_number = dynamic_vars.get("system__caller_id") or dynamic_vars.get("system__called_number")
                                if not phone_number and metadata.get("phone_call"):
                                    phone_call = metadata["phone_call"]
                                    phone_number = phone_call.get("from_number") or phone_call.get("to_number")
                                if not phone_number and metadata.get("whatsapp"):
                                    whatsapp = metadata["whatsapp"]
                                    phone_number = whatsapp.get("phone_number") or whatsapp.get("from") or whatsapp.get("user_id")

                                # Upsert conversation
                                conv_data = {
                                    "id": conversation_id,
                                    "agent_id": agent_id,
                                    "phone_number": phone_number,
                                    "status": conv_detail.get("status", "done"),
                                    "started_at": started_at,
                                    "ended_at": ended_at
                                }

                                if analysis and analysis.get("transcript_summary"):
                                    conv_data["summary"] = analysis.get("transcript_summary")

                                supabase_admin.table("whatsapp_conversations").upsert(
                                    conv_data, on_conflict="id"
                                ).execute()

                                # Insert transcript messages
                                transcript_text = ""
                                if transcript:
                                    existing_msgs = supabase_admin.table("whatsapp_messages").select(
                                        "id, role, content"
                                    ).eq("conversation_id", conversation_id).execute()

                                    if not existing_msgs.data:
                                        messages_to_insert = []
                                        for msg in transcript:
                                            role = msg.get("role", "unknown")
                                            if role == "agent":
                                                role = "assistant"
                                            content = msg.get("message", "") or msg.get("text", "")
                                            if content:
                                                messages_to_insert.append({
                                                    "id": str(uuid.uuid4()),
                                                    "conversation_id": conversation_id,
                                                    "role": role,
                                                    "content": content,
                                                    "timestamp": datetime.utcnow().isoformat()
                                                })

                                        if messages_to_insert:
                                            supabase_admin.table("whatsapp_messages").insert(
                                                messages_to_insert
                                            ).execute()
                                            transcript_text = "\n".join([f"{m['role']}: {m['content']}" for m in messages_to_insert])
                                    else:
                                        transcript_text = "\n".join([f"{m['role']}: {m['content']}" for m in existing_msgs.data])

                                # Generate AI summary if needed
                                if needs_summary and transcript_text.strip():
                                    try:
                                        from app.routers.whatsapp import generate_whatsapp_summary
                                        summary = await generate_whatsapp_summary(conversation_id, transcript_text, owner_user_id)

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

                                            # Extract lead
                                            lead_info = summary.get("lead_info", {})
                                            if lead_info and (lead_info.get("name") or lead_info.get("email") or lead_info.get("phone")):
                                                existing_lead = supabase_admin.table("leads").select(
                                                    "id"
                                                ).eq("conversation_id", conversation_id).limit(1).execute()

                                                if not existing_lead.data:
                                                    supabase_admin.table("leads").insert({
                                                        "conversation_id": conversation_id,
                                                        "source_id": agent_id,
                                                        "source_type": "whatsapp",
                                                        "source_name": agent["name"],
                                                        "user_id": owner_user_id,
                                                        "name": lead_info.get("name"),
                                                        "email": lead_info.get("email"),
                                                        "phone_number": lead_info.get("phone") or phone_number,
                                                        "additional_data": {
                                                            "company": lead_info.get("company"),
                                                            "interest_level": lead_info.get("interest_level"),
                                                            "notes": lead_info.get("notes")
                                                        }
                                                    }).execute()

                                            total_summaries += 1
                                    except Exception as ai_error:
                                        logger.warning(f"AI summary error for {conversation_id}: {ai_error}")

                                total_synced += 1

                        except Exception as agent_error:
                            errors.append(f"Agent {agent_id}: {str(agent_error)}")
                            continue

            except Exception as user_error:
                errors.append(f"User {owner_user_id}: {str(user_error)}")
                continue

        return {
            "success": True,
            "total_synced": total_synced,
            "total_summaries_generated": total_summaries,
            "errors": errors if errors else None
        }

    except Exception as e:
        logger.error(f"Sync all WhatsApp error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sync-all-voice")
async def sync_all_voice_calls(
    auth_data: Dict = Depends(get_current_user),
    generate_summaries: bool = Query(True, description="Generate AI summaries for calls without them")
):
    """
    Admin endpoint to sync Voice Assistant calls for ALL users.
    This fetches calls from VAPI and generates AI summaries.
    """
    try:
        # Get all active VAPI connections
        connections = supabase_admin.table("vapi_connections").select(
            "user_id, api_key"
        ).eq("is_active", True).execute()

        if not connections.data:
            return {"success": True, "message": "No active VAPI connections found", "total_synced": 0}

        total_synced = 0
        total_summaries = 0
        errors = []

        for conn in connections.data:
            owner_user_id = conn["user_id"]
            api_key = conn["api_key"]

            try:
                # Get all voice assistants for this user
                assistants = supabase_admin.table("voice_assistants").select(
                    "id, name"
                ).eq("user_id", owner_user_id).execute()

                if not assistants.data:
                    continue

                async with httpx.AsyncClient(timeout=60.0) as client:
                    for assistant in assistants.data:
                        assistant_id = assistant["id"]

                        try:
                            # Fetch calls from VAPI
                            calls_response = await client.get(
                                "https://api.vapi.ai/call",
                                headers={"Authorization": f"Bearer {api_key}"},
                                params={"assistantId": assistant_id, "limit": 100}
                            )

                            if calls_response.status_code != 200:
                                continue

                            calls = calls_response.json()
                            if isinstance(calls, dict):
                                calls = calls.get("calls", []) or calls.get("data", [])

                            for call in calls:
                                call_id = call.get("id")
                                if not call_id:
                                    continue

                                # Check if call exists
                                existing = supabase_admin.table("voice_assistant_calls").select(
                                    "id, summary"
                                ).eq("id", call_id).execute()

                                is_new = not existing.data
                                needs_summary = generate_summaries and (is_new or not existing.data[0].get("summary") if existing.data else True)

                                # Parse call data
                                started_at = call.get("startedAt") or call.get("createdAt")
                                ended_at = call.get("endedAt")
                                duration = None
                                if started_at and ended_at:
                                    try:
                                        start_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                                        end_dt = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
                                        duration = int((end_dt - start_dt).total_seconds())
                                    except:
                                        pass

                                phone_number = call.get("customer", {}).get("number") if call.get("customer") else None

                                # Upsert call
                                call_data = {
                                    "id": call_id,
                                    "assistant_id": assistant_id,
                                    "phone_number": phone_number,
                                    "status": call.get("status", "ended"),
                                    "started_at": started_at,
                                    "ended_at": ended_at,
                                    "duration_seconds": duration,
                                    "recording_url": call.get("recordingUrl"),
                                    "cost": call.get("cost")
                                }

                                supabase_admin.table("voice_assistant_calls").upsert(
                                    call_data, on_conflict="id"
                                ).execute()

                                # Get transcript
                                transcript_text = ""
                                transcript = call.get("transcript") or call.get("messages", [])

                                if transcript:
                                    if isinstance(transcript, str):
                                        transcript_text = transcript
                                    elif isinstance(transcript, list):
                                        # Store transcript messages
                                        existing_transcripts = supabase_admin.table("voice_call_transcripts").select(
                                            "id"
                                        ).eq("call_id", call_id).execute()

                                        if not existing_transcripts.data:
                                            for msg in transcript:
                                                role = msg.get("role", "unknown")
                                                content = msg.get("content") or msg.get("message") or msg.get("text", "")
                                                if content:
                                                    supabase_admin.table("voice_call_transcripts").insert({
                                                        "call_id": call_id,
                                                        "role": role,
                                                        "content": content,
                                                        "timestamp": msg.get("timestamp") or datetime.utcnow().isoformat()
                                                    }).execute()

                                        transcript_text = "\n".join([
                                            f"{m.get('role', 'unknown')}: {m.get('content') or m.get('message') or m.get('text', '')}"
                                            for m in transcript if m.get('content') or m.get('message') or m.get('text')
                                        ])

                                # Generate AI summary if needed
                                if needs_summary and transcript_text.strip():
                                    try:
                                        from app.routers.voice import generate_call_summary
                                        summary = await generate_call_summary(call_id, transcript_text, owner_user_id)

                                        if summary:
                                            supabase_admin.table("voice_assistant_calls").update({
                                                "summary": summary.get("summary"),
                                                "key_points": summary.get("key_points"),
                                                "action_items": summary.get("action_items"),
                                                "sentiment": summary.get("sentiment"),
                                                "sentiment_notes": summary.get("sentiment_notes"),
                                                "conversation_outcome": summary.get("conversation_outcome"),
                                                "topics_discussed": summary.get("topics_discussed"),
                                                "lead_info": summary.get("lead_info")
                                            }).eq("id", call_id).execute()

                                            # Extract lead
                                            lead_info = summary.get("lead_info", {})
                                            if lead_info and (lead_info.get("name") or lead_info.get("email") or lead_info.get("phone")):
                                                existing_lead = supabase_admin.table("leads").select(
                                                    "id"
                                                ).eq("conversation_id", call_id).limit(1).execute()

                                                if not existing_lead.data:
                                                    supabase_admin.table("leads").insert({
                                                        "conversation_id": call_id,
                                                        "source_id": assistant_id,
                                                        "source_type": "voice",
                                                        "source_name": assistant["name"],
                                                        "user_id": owner_user_id,
                                                        "name": lead_info.get("name"),
                                                        "email": lead_info.get("email"),
                                                        "phone_number": lead_info.get("phone") or phone_number,
                                                        "additional_data": {
                                                            "company": lead_info.get("company"),
                                                            "interest_level": lead_info.get("interest_level"),
                                                            "notes": lead_info.get("notes")
                                                        }
                                                    }).execute()

                                            total_summaries += 1
                                    except Exception as ai_error:
                                        logger.warning(f"AI summary error for call {call_id}: {ai_error}")

                                total_synced += 1

                        except Exception as assistant_error:
                            errors.append(f"Assistant {assistant_id}: {str(assistant_error)}")
                            continue

            except Exception as user_error:
                errors.append(f"User {owner_user_id}: {str(user_error)}")
                continue

        return {
            "success": True,
            "total_synced": total_synced,
            "total_summaries_generated": total_summaries,
            "errors": errors if errors else None
        }

    except Exception as e:
        logger.error(f"Sync all voice calls error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sync-all-chatbots")
async def sync_all_chatbot_summaries(
    auth_data: Dict = Depends(get_current_user)
):
    """
    Admin endpoint to generate AI summaries for ALL chatbot conversations without summaries.
    """
    try:
        # Get all conversations without summaries that have messages
        conversations = supabase_admin.table("conversations").select(
            "id, chatbot_id, visitor_id"
        ).is_("summary", "null").execute()

        if not conversations.data:
            return {"success": True, "message": "No conversations need summaries", "total_processed": 0}

        total_processed = 0
        total_summaries = 0
        errors = []

        for conv in conversations.data:
            conversation_id = conv["id"]
            chatbot_id = conv["chatbot_id"]

            try:
                # Get chatbot owner
                chatbot = supabase_admin.table("chatbots").select(
                    "user_id, name"
                ).eq("id", chatbot_id).single().execute()

                if not chatbot.data:
                    continue

                owner_user_id = chatbot.data["user_id"]

                # Check if conversation has messages
                messages = supabase_admin.table("messages").select(
                    "role, content"
                ).eq("conversation_id", conversation_id).execute()

                if not messages.data or len(messages.data) < 2:
                    continue

                # Generate summary
                from app.routers.chat import generate_conversation_summary
                summary = await generate_conversation_summary(conversation_id, owner_user_id)

                if summary:
                    supabase_admin.table("conversations").update({
                        "summary": summary.get("summary"),
                        "key_points": summary.get("key_points"),
                        "action_items": summary.get("action_items"),
                        "sentiment": summary.get("sentiment"),
                        "sentiment_notes": summary.get("sentiment_notes"),
                        "conversation_outcome": summary.get("conversation_outcome"),
                        "topics_discussed": summary.get("topics_discussed"),
                        "lead_info": summary.get("lead_info")
                    }).eq("id", conversation_id).execute()

                    # Extract lead
                    lead_info = summary.get("lead_info", {})
                    if lead_info and (lead_info.get("name") or lead_info.get("email") or lead_info.get("phone")):
                        existing_lead = supabase_admin.table("leads").select(
                            "id"
                        ).eq("conversation_id", conversation_id).limit(1).execute()

                        if not existing_lead.data:
                            supabase_admin.table("leads").insert({
                                "conversation_id": conversation_id,
                                "source_id": chatbot_id,
                                "source_type": "chatbot",
                                "source_name": chatbot.data["name"],
                                "user_id": owner_user_id,
                                "name": lead_info.get("name"),
                                "email": lead_info.get("email"),
                                "phone_number": lead_info.get("phone"),
                                "additional_data": {
                                    "company": lead_info.get("company"),
                                    "interest_level": lead_info.get("interest_level"),
                                    "notes": lead_info.get("notes")
                                }
                            }).execute()

                    total_summaries += 1

                total_processed += 1

            except Exception as conv_error:
                errors.append(f"Conversation {conversation_id}: {str(conv_error)}")
                continue

        return {
            "success": True,
            "total_processed": total_processed,
            "total_summaries_generated": total_summaries,
            "errors": errors if errors else None
        }

    except Exception as e:
        logger.error(f"Sync all chatbot summaries error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sync-all")
async def sync_everything(
    auth_data: Dict = Depends(get_current_user),
    generate_summaries: bool = Query(True, description="Generate AI summaries")
):
    """
    Admin endpoint to sync ALL conversations/calls for ALL users.
    This syncs WhatsApp, Voice, and Chatbot conversations.
    """
    results = {
        "whatsapp": None,
        "voice": None,
        "chatbots": None
    }

    try:
        # Sync WhatsApp
        try:
            wa_result = await sync_all_whatsapp_conversations(auth_data, generate_summaries)
            results["whatsapp"] = wa_result
        except Exception as e:
            results["whatsapp"] = {"error": str(e)}

        # Sync Voice
        try:
            voice_result = await sync_all_voice_calls(auth_data, generate_summaries)
            results["voice"] = voice_result
        except Exception as e:
            results["voice"] = {"error": str(e)}

        # Sync Chatbots
        try:
            chatbot_result = await sync_all_chatbot_summaries(auth_data)
            results["chatbots"] = chatbot_result
        except Exception as e:
            results["chatbots"] = {"error": str(e)}

        return {
            "success": True,
            "results": results
        }

    except Exception as e:
        logger.error(f"Sync everything error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
