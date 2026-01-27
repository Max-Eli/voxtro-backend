"""Voice assistant endpoints - VAPI integration"""
from fastapi import APIRouter, HTTPException, Depends
from typing import Dict
import logging
import httpx

from app.models.voice import (
    VoiceAssistantSyncResponse,
    VoiceAssistantUpdate,
    VoiceConnectionValidation,
    FetchVapiCallsRequest,
    FetchVapiCallsResponse
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

        # Get ACTIVE VAPI connection (user may have multiple)
        conn_result = supabase_admin.table("voice_connections").select(
            "*"
        ).eq("user_id", user_id).eq("is_active", True).single().execute()

        if not conn_result.data:
            # Fallback: try to get any connection if no active one
            conn_result = supabase_admin.table("voice_connections").select(
                "*"
            ).eq("user_id", user_id).limit(1).execute()

            if not conn_result.data or len(conn_result.data) == 0:
                raise HTTPException(status_code=404, detail="VAPI connection not found")

            conn_data = conn_result.data[0]
        else:
            conn_data = conn_result.data

        # Use api_key for server-side API calls (NOT public_key which is for client-side)
        api_key = conn_data["api_key"]

        # Fetch assistants from VAPI
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.vapi.ai/assistant",
                headers={"Authorization": f"Bearer {api_key}"}
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

        # Get ACTIVE VAPI connection
        conn_result = supabase_admin.table("voice_connections").select(
            "api_key"
        ).eq("user_id", user_id).eq("is_active", True).single().execute()

        if not conn_result.data:
            raise HTTPException(status_code=404, detail="No active VAPI connection found")

        api_key = conn_result.data["api_key"]

        # Update in VAPI
        update_data = updates.dict(exclude_unset=True)

        async with httpx.AsyncClient() as client:
            response = await client.patch(
                f"https://api.vapi.ai/assistant/{assistant_id}",
                headers={"Authorization": f"Bearer {api_key}"},
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
                headers={"Authorization": f"Bearer {validation.api_key}"}
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


@router.get("/calls")
async def get_voice_calls(
    assistant_id: str = None,
    limit: int = 50,
    auth_data: Dict = Depends(get_current_user)
):
    """Get voice call history with summaries"""
    try:
        user_id = auth_data["user_id"]

        # Get user's assistants
        if assistant_id:
            # Verify ownership
            assistant_check = supabase_admin.table("voice_assistants").select("id").eq(
                "id", assistant_id
            ).eq("user_id", user_id).single().execute()
            
            if not assistant_check.data:
                raise HTTPException(status_code=403, detail="Assistant not found or access denied")
            
            assistant_ids = [assistant_id]
        else:
            assistants_result = supabase_admin.table("voice_assistants").select("id").eq(
                "user_id", user_id
            ).execute()
            assistant_ids = [a["id"] for a in (assistants_result.data or [])]

        if not assistant_ids:
            return {"calls": []}

        # Get calls with all data
        calls_result = supabase_admin.table("voice_assistant_calls").select(
            "id, assistant_id, customer_id, status, started_at, ended_at, duration_seconds, cost, "
            "summary, key_points, action_items, sentiment, sentiment_notes, call_outcome, topics_discussed, lead_info"
        ).in_("assistant_id", assistant_ids).order("started_at", desc=True).limit(limit).execute()

        return {"calls": calls_result.data or []}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get calls error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/calls/{call_id}")
async def get_call_details(
    call_id: str,
    auth_data: Dict = Depends(get_current_user)
):
    """Get detailed call information including transcript and summary"""
    try:
        user_id = auth_data["user_id"]

        # Get call
        call_result = supabase_admin.table("voice_assistant_calls").select("*").eq(
            "id", call_id
        ).single().execute()

        if not call_result.data:
            raise HTTPException(status_code=404, detail="Call not found")

        # Verify ownership via assistant
        assistant_result = supabase_admin.table("voice_assistants").select("user_id").eq(
            "id", call_result.data["assistant_id"]
        ).single().execute()

        if not assistant_result.data or assistant_result.data["user_id"] != user_id:
            raise HTTPException(status_code=403, detail="Access denied")

        # Get transcript
        transcript_result = supabase_admin.table("voice_assistant_transcripts").select(
            "role, content, timestamp"
        ).eq("call_id", call_id).order("timestamp").execute()

        # Get recording
        recording_result = supabase_admin.table("voice_assistant_recordings").select(
            "recording_url"
        ).eq("call_id", call_id).single().execute()

        return {
            "call": call_result.data,
            "transcript": transcript_result.data or [],
            "recording_url": recording_result.data.get("recording_url") if recording_result.data else None
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get call details error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/calls/{call_id}/summarize")
async def summarize_call(
    call_id: str,
    auth_data: Dict = Depends(get_current_user)
):
    """Generate or regenerate AI summary for a call"""
    try:
        user_id = auth_data["user_id"]

        # Get call
        call_result = supabase_admin.table("voice_assistant_calls").select(
            "id, assistant_id"
        ).eq("id", call_id).single().execute()

        if not call_result.data:
            raise HTTPException(status_code=404, detail="Call not found")

        # Verify ownership via assistant
        assistant_result = supabase_admin.table("voice_assistants").select("user_id").eq(
            "id", call_result.data["assistant_id"]
        ).single().execute()

        if not assistant_result.data or assistant_result.data["user_id"] != user_id:
            raise HTTPException(status_code=403, detail="Access denied")

        # Get transcript
        transcript_result = supabase_admin.table("voice_assistant_transcripts").select(
            "role, content"
        ).eq("call_id", call_id).order("timestamp").execute()

        if not transcript_result.data:
            raise HTTPException(status_code=400, detail="No transcript available for this call")

        # Build transcript text
        transcript_text = "\n".join([
            f"{msg['role']}: {msg['content']}"
            for msg in transcript_result.data
        ])

        # Generate summary
        from app.routers.webhooks import generate_call_summary
        summary = await generate_call_summary(call_id, transcript_text, user_id)

        if not summary:
            raise HTTPException(status_code=500, detail="Failed to generate summary")

        # Save summary
        supabase_admin.table("voice_assistant_calls").update({
            "summary": summary.get("summary"),
            "key_points": summary.get("key_points"),
            "action_items": summary.get("action_items"),
            "sentiment": summary.get("sentiment"),
            "sentiment_notes": summary.get("sentiment_notes"),
            "call_outcome": summary.get("call_outcome"),
            "topics_discussed": summary.get("topics_discussed"),
            "lead_info": summary.get("lead_info")
        }).eq("id", call_id).execute()

        return {
            "success": True,
            "summary": summary
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Summarize call error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/fetch-calls", response_model=FetchVapiCallsResponse)
async def fetch_vapi_calls(
    request: FetchVapiCallsRequest
):
    """
    Fetch and sync calls from VAPI API for a specific assistant.
    Used by customer portal to get call history.

    This endpoint:
    1. Syncs assistants from VAPI to ensure IDs are correct
    2. Fetches all calls from VAPI
    3. Matches calls by assistant name (handles ID mismatches)
    4. Syncs matched calls to database
    """
    try:
        assistant_id = request.assistant_id

        # Get the assistant to find the owner (user_id) and name
        assistant_result = supabase_admin.table("voice_assistants").select(
            "user_id, name, org_id"
        ).eq("id", assistant_id).single().execute()

        if not assistant_result.data:
            logger.error(f"Assistant not found: {assistant_id}")
            raise HTTPException(status_code=404, detail="Voice assistant not found")

        assistant_data = assistant_result.data
        owner_user_id = assistant_data["user_id"]
        assistant_name = assistant_data.get("name", "Unknown")
        assistant_org_id = assistant_data.get("org_id")

        # Get the VAPI API key from voice_connections
        conn_result = supabase_admin.table("voice_connections").select(
            "api_key"
        ).eq("user_id", owner_user_id).eq("is_active", True).limit(1).execute()

        if not conn_result.data or len(conn_result.data) == 0:
            logger.error(f"No voice connection found for user: {owner_user_id}")
            raise HTTPException(
                status_code=404,
                detail="No active voice connection found for this assistant owner"
            )

        api_key = conn_result.data[0]["api_key"]

        async with httpx.AsyncClient(timeout=60.0) as client:
            # Step 1: Sync assistants from VAPI first
            logger.info(f"Syncing assistants from VAPI for user {owner_user_id}")
            assistants_response = await client.get(
                "https://api.vapi.ai/assistant",
                headers={"Authorization": f"Bearer {api_key}"}
            )

            vapi_assistant_id = assistant_id  # Default to the ID we have
            assistant_ids_in_vapi = []

            if assistants_response.status_code == 200:
                vapi_assistants = assistants_response.json()
                logger.info(f"Found {len(vapi_assistants)} assistants in VAPI")

                # Build a map of assistant names to VAPI IDs
                name_to_vapi_id = {}
                for va in vapi_assistants:
                    va_name = va.get("name", "")
                    va_id = va.get("id")
                    if va_name and va_id:
                        name_to_vapi_id[va_name.lower().strip()] = va_id
                        assistant_ids_in_vapi.append(va_id)

                        # Sync assistant to database
                        voice_data = va.get("voice", {}) or {}
                        model_data = va.get("model", {}) or {}
                        supabase_admin.table("voice_assistants").upsert({
                            "id": va_id,
                            "user_id": owner_user_id,
                            "name": va.get("name") or "Unnamed",
                            "first_message": va.get("firstMessage"),
                            "voice_provider": voice_data.get("provider"),
                            "voice_id": voice_data.get("voiceId"),
                            "model_provider": model_data.get("provider"),
                            "model": model_data.get("model"),
                            "org_id": va.get("orgId"),
                        }, on_conflict="id").execute()

                # Find the correct VAPI ID by matching name
                search_name = assistant_name.lower().strip()
                if search_name in name_to_vapi_id:
                    vapi_assistant_id = name_to_vapi_id[search_name]
                    logger.info(f"Matched assistant '{assistant_name}' to VAPI ID: {vapi_assistant_id}")

                    # Update our assistant record if ID changed
                    if vapi_assistant_id != assistant_id:
                        logger.info(f"Updating assistant ID from {assistant_id} to {vapi_assistant_id}")
                        # Update customer assignments to point to new ID
                        supabase_admin.table("customer_assistant_assignments").update({
                            "assistant_id": vapi_assistant_id
                        }).eq("assistant_id", assistant_id).execute()
                else:
                    logger.warning(f"Could not find assistant '{assistant_name}' in VAPI")

            # Step 2: Fetch ALL calls from VAPI
            logger.info("Fetching all calls from VAPI")
            calls_response = await client.get(
                "https://api.vapi.ai/call?limit=1000",
                headers={"Authorization": f"Bearer {api_key}"}
            )

            if calls_response.status_code != 200:
                logger.error(f"VAPI calls API error: {calls_response.status_code}")
                raise HTTPException(status_code=500, detail="Failed to fetch calls from voice service")

            all_calls = calls_response.json()
            total_all_calls = len(all_calls)
            logger.info(f"Total calls in VAPI: {total_all_calls}")

            # Filter calls for the target assistant (using matched VAPI ID)
            vapi_calls = [c for c in all_calls if c.get("assistantId") == vapi_assistant_id]
            logger.info(f"Calls for assistant {vapi_assistant_id}: {len(vapi_calls)}")

        # Find customer assigned to this assistant (check both old and new IDs)
        assignment_result = supabase_admin.table("customer_assistant_assignments").select(
            "customer_id"
        ).eq("assistant_id", vapi_assistant_id).limit(1).maybe_single().execute()

        # Also check with original ID if no match
        if not assignment_result.data and vapi_assistant_id != assistant_id:
            assignment_result = supabase_admin.table("customer_assistant_assignments").select(
                "customer_id"
            ).eq("assistant_id", assistant_id).limit(1).maybe_single().execute()

        customer_id = assignment_result.data["customer_id"] if assignment_result.data else None

        # Sync calls to database
        synced_count = 0
        for call in vapi_calls:
            # Calculate duration
            duration_seconds = 0
            if call.get("startedAt") and call.get("endedAt"):
                from datetime import datetime
                try:
                    start_time = datetime.fromisoformat(call["startedAt"].replace("Z", "+00:00"))
                    end_time = datetime.fromisoformat(call["endedAt"].replace("Z", "+00:00"))
                    duration_seconds = int((end_time - start_time).total_seconds())
                except Exception as e:
                    logger.warning(f"Error calculating duration: {e}")

            call_data = {
                "id": call["id"],
                "assistant_id": vapi_assistant_id,  # Use the correct VAPI ID
                "customer_id": customer_id,
                "phone_number": call.get("customer", {}).get("number") or call.get("phoneNumber", {}).get("number"),
                "started_at": call.get("startedAt"),
                "ended_at": call.get("endedAt"),
                "duration_seconds": duration_seconds,
                "status": call.get("status", "completed"),
                "call_type": call.get("type", "inbound"),
            }

            # Upsert call record
            try:
                supabase_admin.table("voice_assistant_calls").upsert(
                    call_data,
                    on_conflict="id"
                ).execute()
                synced_count += 1

                # Sync transcript if available
                artifact = call.get("artifact", {})
                messages = artifact.get("messages", [])

                for msg in messages:
                    role = "assistant" if msg.get("role") == "bot" else msg.get("role")
                    content = msg.get("message")

                    if role in ("user", "assistant") and content:
                        # Check if transcript already exists
                        existing = supabase_admin.table("voice_assistant_transcripts").select(
                            "id"
                        ).eq("call_id", call["id"]).eq("content", content).eq("role", role).maybe_single().execute()

                        if not existing.data:
                            supabase_admin.table("voice_assistant_transcripts").insert({
                                "call_id": call["id"],
                                "role": role,
                                "content": content,
                                "timestamp": msg.get("time") or call.get("startedAt"),
                            }).execute()

                # Sync recording if available
                recording_url = artifact.get("recordingUrl")
                if recording_url:
                    existing_recording = supabase_admin.table("voice_assistant_recordings").select(
                        "id"
                    ).eq("call_id", call["id"]).maybe_single().execute()

                    if not existing_recording.data:
                        supabase_admin.table("voice_assistant_recordings").insert({
                            "call_id": call["id"],
                            "recording_url": recording_url,
                        }).execute()

            except Exception as e:
                logger.error(f"Error syncing call {call['id']}: {e}")
                continue

        return FetchVapiCallsResponse(
            success=True,
            total_from_vapi=len(vapi_calls),
            synced_count=synced_count,
            assistant_name=assistant_name,
            total_all_calls=total_all_calls,
            assistant_ids_in_vapi=assistant_ids_in_vapi
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching VAPI calls: {e}")
        raise HTTPException(status_code=500, detail=str(e))
