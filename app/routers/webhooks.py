"""Webhooks from external services"""
from fastapi import APIRouter, HTTPException
import logging
from typing import Dict, Any

from app.models.voice import VapiWebhookPayload
from app.database import supabase_admin

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/vapi")
async def vapi_webhook(payload: VapiWebhookPayload):
    """Handle VAPI webhooks (end-of-call events)"""
    try:
        message = payload.message
        message_type = message.get("type")

        if message_type == "end-of-call-report":
            call = payload.call or {}
            artifact = payload.artifact or {}

            # Save call record
            call_data = {
                "id": call.get("id"),
                "assistant_id": call.get("assistantId"),
                "customer_id": call.get("customer", {}).get("number"),
                "status": call.get("status"),
                "started_at": call.get("startedAt"),
                "ended_at": call.get("endedAt"),
                "duration_seconds": call.get("duration"),
                "cost": call.get("cost")
            }

            supabase_admin.table("voice_assistant_calls").upsert(
                call_data, on_conflict="id"
            ).execute()

            # Save transcript
            if artifact.get("transcript"):
                transcript = artifact["transcript"]
                for msg in transcript.get("messages", []):
                    supabase_admin.table("voice_assistant_transcripts").insert({
                        "call_id": call.get("id"),
                        "role": msg.get("role"),
                        "content": msg.get("message"),
                        "timestamp": msg.get("time")
                    }).execute()

            # Save recording
            if artifact.get("recordingUrl"):
                supabase_admin.table("voice_assistant_recordings").insert({
                    "call_id": call.get("id"),
                    "recording_url": artifact["recordingUrl"]
                }).execute()

        return {"success": True}

    except Exception as e:
        logger.error(f"VAPI webhook error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
