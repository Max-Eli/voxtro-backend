"""Voice assistant endpoints - VAPI integration"""
from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Optional
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
from app.routers.webhooks import generate_call_summary

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/sync", response_model=VoiceAssistantSyncResponse)
async def sync_voice_assistants(
    auth_data: Dict = Depends(get_current_user)
):
    """
    Sync voice assistants from ALL VAPI organizations.

    This endpoint syncs assistants from ALL voice connections for the user,
    not just the active one. Each assistant is stored with its proper org_id
    to maintain organization separation.

    Also updates the voice_connections.org_id to match the VAPI org ID for
    proper filtering in the frontend.
    """
    try:
        user_id = auth_data["user_id"]

        # Get ALL VAPI connections for this user (not just active)
        # Include the connection ID so we can update org_id if needed
        all_connections = supabase_admin.table("voice_connections").select(
            "id, api_key, org_name, org_id"
        ).eq("user_id", user_id).execute()

        if not all_connections.data or len(all_connections.data) == 0:
            raise HTTPException(status_code=404, detail="VAPI connection not found")

        logger.info(f"Syncing from {len(all_connections.data)} VAPI connections for user {user_id}")

        all_assistants = []

        async with httpx.AsyncClient(timeout=60.0) as client:
            # Sync assistants from EACH VAPI connection
            for conn in all_connections.data:
                conn_id = conn["id"]
                api_key = conn["api_key"]
                org_name = conn.get("org_name", "Unknown")
                stored_org_id = conn.get("org_id")

                logger.info(f"Fetching assistants from VAPI org: {org_name}")

                response = await client.get(
                    "https://api.vapi.ai/assistant",
                    headers={"Authorization": f"Bearer {api_key}"}
                )

                if response.status_code != 200:
                    logger.warning(f"Failed to sync from VAPI org {org_name}: {response.status_code}")
                    continue

                assistants = response.json()
                logger.info(f"Found {len(assistants)} assistants in VAPI org: {org_name}")

                # Get the VAPI org ID from the first assistant (they all belong to the same org)
                vapi_org_id = None
                if assistants and len(assistants) > 0:
                    vapi_org_id = assistants[0].get("orgId")

                # Update voice_connection.org_id if it's different or NULL
                if vapi_org_id and vapi_org_id != stored_org_id:
                    logger.info(f"Updating connection {conn_id} org_id from '{stored_org_id}' to '{vapi_org_id}'")
                    supabase_admin.table("voice_connections").update({
                        "org_id": vapi_org_id
                    }).eq("id", conn_id).execute()

                # Upsert each assistant with proper org_id
                for assistant in assistants:
                    voice_data = assistant.get("voice", {}) or {}
                    model_data = assistant.get("model", {}) or {}
                    assistant_org_id = assistant.get("orgId") or vapi_org_id

                    supabase_admin.table("voice_assistants").upsert({
                        "id": assistant["id"],
                        "user_id": user_id,
                        "name": assistant.get("name"),
                        "first_message": assistant.get("firstMessage"),
                        "voice_provider": voice_data.get("provider"),
                        "voice_id": voice_data.get("voiceId"),
                        "model_provider": model_data.get("provider"),
                        "model": model_data.get("model"),
                        "phone_number": assistant.get("phoneNumber"),
                        "org_id": assistant_org_id,  # Store VAPI org ID for proper tracking
                    }, on_conflict="id").execute()

                    all_assistants.append(assistant)

        return VoiceAssistantSyncResponse(
            count=len(all_assistants),
            assistants=all_assistants
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

        # Get ALL VAPI connections for this user (not just active)
        # User may have assistants in different VAPI organizations
        all_connections = supabase_admin.table("voice_connections").select(
            "id, api_key, org_name, org_id, is_active"
        ).eq("user_id", owner_user_id).execute()

        if not all_connections.data or len(all_connections.data) == 0:
            logger.error(f"No voice connections found for user: {owner_user_id}")
            raise HTTPException(
                status_code=404,
                detail="No voice connection found for this assistant owner"
            )

        logger.info(f"User {owner_user_id} has {len(all_connections.data)} VAPI connections")

        # Try each connection to find the one containing this assistant
        api_key = None
        vapi_assistant_id = assistant_id  # Default to the ID we have
        assistant_ids_in_vapi = []
        found_in_org = None

        async with httpx.AsyncClient(timeout=60.0) as client:
            # Step 1: Search through ALL VAPI connections to find the assistant
            for conn in all_connections.data:
                conn_id = conn["id"]
                conn_api_key = conn["api_key"]
                conn_org_name = conn.get("org_name", "Unknown")
                conn_stored_org_id = conn.get("org_id")
                logger.info(f"Checking VAPI org '{conn_org_name}' for assistant '{assistant_name}'")

                assistants_response = await client.get(
                    "https://api.vapi.ai/assistant",
                    headers={"Authorization": f"Bearer {conn_api_key}"}
                )

                if assistants_response.status_code != 200:
                    logger.warning(f"Failed to fetch assistants from org '{conn_org_name}'")
                    continue

                vapi_assistants = assistants_response.json()
                logger.info(f"Org '{conn_org_name}' has {len(vapi_assistants)} assistants")

                # Get the VAPI org ID from the first assistant
                vapi_org_id = vapi_assistants[0].get("orgId") if vapi_assistants else None

                # Update voice_connection.org_id if it's different or NULL
                if vapi_org_id and vapi_org_id != conn_stored_org_id:
                    logger.info(f"Updating connection {conn_id} org_id from '{conn_stored_org_id}' to '{vapi_org_id}'")
                    supabase_admin.table("voice_connections").update({
                        "org_id": vapi_org_id
                    }).eq("id", conn_id).execute()

                # Check if our target assistant is in this org (by name)
                search_name = assistant_name.lower().strip()
                for va in vapi_assistants:
                    va_name = va.get("name", "")
                    va_id = va.get("id")
                    if va_name and va_id:
                        if va_name.lower().strip() == search_name:
                            # Found the assistant in this org!
                            api_key = conn_api_key
                            vapi_assistant_id = va_id
                            found_in_org = conn_org_name
                            logger.info(f"Found assistant '{assistant_name}' in org '{conn_org_name}' with ID: {va_id}")

                            # Collect all assistant IDs from this org
                            for a in vapi_assistants:
                                if a.get("id"):
                                    assistant_ids_in_vapi.append(a.get("id"))

                            # Sync all assistants from this org to database with proper org_id
                            for a in vapi_assistants:
                                voice_data = a.get("voice", {}) or {}
                                model_data = a.get("model", {}) or {}
                                assistant_vapi_org_id = a.get("orgId") or vapi_org_id
                                supabase_admin.table("voice_assistants").upsert({
                                    "id": a.get("id"),
                                    "user_id": owner_user_id,
                                    "name": a.get("name") or "Unnamed",
                                    "first_message": a.get("firstMessage"),
                                    "voice_provider": voice_data.get("provider"),
                                    "voice_id": voice_data.get("voiceId"),
                                    "model_provider": model_data.get("provider"),
                                    "model": model_data.get("model"),
                                    "org_id": assistant_vapi_org_id,
                                }, on_conflict="id").execute()

                            break

                if api_key:
                    break  # Found the assistant, stop searching

            # If assistant not found in any org, fall back to active connection
            if not api_key:
                logger.warning(f"Assistant '{assistant_name}' not found in any VAPI org, using active connection")
                active_conn = next((c for c in all_connections.data if c.get("is_active")), all_connections.data[0])
                api_key = active_conn["api_key"]
                found_in_org = active_conn.get("org_name", "Unknown")

            # Update our assistant record if ID changed
            if vapi_assistant_id != assistant_id:
                logger.info(f"Updating assistant ID from {assistant_id} to {vapi_assistant_id}")
                # Update customer assignments to point to new ID
                supabase_admin.table("customer_assistant_assignments").update({
                    "assistant_id": vapi_assistant_id
                }).eq("assistant_id", assistant_id).execute()

            # Step 2: Fetch calls for this specific assistant from VAPI
            # Use assistantId parameter for more reliable results
            logger.info(f"Fetching calls for assistant {vapi_assistant_id} from VAPI org '{found_in_org}'")
            calls_response = await client.get(
                f"https://api.vapi.ai/call?assistantId={vapi_assistant_id}&limit=1000",
                headers={"Authorization": f"Bearer {api_key}"}
            )

            if calls_response.status_code != 200:
                logger.error(f"VAPI calls API error: {calls_response.status_code} - {calls_response.text}")
                raise HTTPException(status_code=500, detail="Failed to fetch calls from voice service")

            vapi_calls = calls_response.json()
            total_all_calls = len(vapi_calls)
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
            transcript_synced_count = 0
            summary_generated_count = 0

            for call in vapi_calls:
                call_id = call["id"]

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
                    "id": call_id,
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

                    # Check if we already have transcripts for this call
                    existing_transcripts = supabase_admin.table("voice_assistant_transcripts").select(
                        "id"
                    ).eq("call_id", call_id).limit(1).execute()

                    # Get artifact from list response or fetch individual call details
                    artifact = call.get("artifact", {})
                    messages = artifact.get("messages", [])

                    # If no messages in list response and no existing transcripts, fetch individual call details
                    if not messages and not existing_transcripts.data:
                        logger.info(f"Fetching individual call details for {call_id}")
                        try:
                            call_detail_response = await client.get(
                                f"https://api.vapi.ai/call/{call_id}",
                                headers={"Authorization": f"Bearer {api_key}"}
                            )
                            if call_detail_response.status_code == 200:
                                call_detail = call_detail_response.json()
                                artifact = call_detail.get("artifact", {})
                                messages = artifact.get("messages", [])
                                logger.info(f"Got {len(messages)} messages from call details for {call_id}")
                        except Exception as detail_error:
                            logger.warning(f"Failed to fetch call details for {call_id}: {detail_error}")

                    # Sync transcript messages
                    transcript_text = ""
                    for msg in messages:
                        role = "assistant" if msg.get("role") == "bot" else msg.get("role")
                        content = msg.get("message")

                        if role in ("user", "assistant") and content:
                            transcript_text += f"{role}: {content}\n"

                            # Check if transcript already exists
                            existing = supabase_admin.table("voice_assistant_transcripts").select(
                                "id"
                            ).eq("call_id", call_id).eq("content", content).eq("role", role).maybe_single().execute()

                            if not existing.data:
                                supabase_admin.table("voice_assistant_transcripts").insert({
                                    "call_id": call_id,
                                    "role": role,
                                    "content": content,
                                    "timestamp": msg.get("time") or call.get("startedAt"),
                                }).execute()
                                transcript_synced_count += 1

                    # Sync recording if available
                    recording_url = artifact.get("recordingUrl")
                    if recording_url:
                        existing_recording = supabase_admin.table("voice_assistant_recordings").select(
                            "id"
                        ).eq("call_id", call_id).maybe_single().execute()

                        if not existing_recording.data:
                            supabase_admin.table("voice_assistant_recordings").insert({
                                "call_id": call_id,
                                "recording_url": recording_url,
                            }).execute()

                    # Generate AI summary if we have transcript but no existing summary
                    if transcript_text:
                        # Check if call already has a summary
                        call_record = supabase_admin.table("voice_assistant_calls").select(
                            "summary"
                        ).eq("id", call_id).single().execute()

                        if call_record.data and not call_record.data.get("summary"):
                            logger.info(f"Generating AI summary for call {call_id}")
                            try:
                                summary = await generate_call_summary(call_id, transcript_text, owner_user_id)
                                if summary:
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
                                    summary_generated_count += 1
                                    logger.info(f"Generated summary for call {call_id}")
                            except Exception as summary_error:
                                logger.warning(f"Failed to generate summary for call {call_id}: {summary_error}")

                except Exception as e:
                    logger.error(f"Error syncing call {call_id}: {e}")
                    continue

            logger.info(f"Sync complete: {synced_count} calls, {transcript_synced_count} transcript messages, {summary_generated_count} summaries generated")

        return FetchVapiCallsResponse(
            success=True,
            total_from_vapi=len(vapi_calls),
            synced_count=synced_count,
            assistant_name=assistant_name,
            vapi_org_name=found_in_org,
            matched_vapi_id=vapi_assistant_id,
            total_all_calls=total_all_calls,
            assistant_ids_in_vapi=assistant_ids_in_vapi
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching VAPI calls: {e}")
        raise HTTPException(status_code=500, detail=str(e))
