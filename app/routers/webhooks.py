"""Webhooks from external services"""
from fastapi import APIRouter, HTTPException
import logging
from typing import Dict, Any, Optional

from app.models.voice import VapiWebhookPayload
from app.database import supabase_admin
from app.services.ai_service import call_mistral

logger = logging.getLogger(__name__)
router = APIRouter()


async def generate_call_summary(call_id: str, transcript_text: str, user_id: str) -> Optional[Dict[str, Any]]:
    """
    Generate AI summary of a voice call transcript using Mistral

    Args:
        call_id: The call ID
        transcript_text: Full transcript text
        user_id: User ID (kept for interface compatibility)

    Returns:
        Summary dict with summary, key_points, action_items, sentiment, lead_info
    """
    try:
        import json
        import re

        system_message = (
            "You are a JSON API that analyzes voice call transcripts. "
            "You MUST respond with ONLY a valid JSON object — no markdown, no explanation, no extra text. "
            "Every field must be populated. Use \"unknown\" or empty arrays if information is not available."
        )

        user_prompt = f"""Analyze this voice call transcript and return a JSON object with these exact fields:

- "summary": A brief 2-3 sentence summary of what happened in the call.
- "key_points": An array of the key details discussed (e.g. ["Caller asked about pricing", "Agent offered a callback"]).
- "action_items": An array of follow-up actions needed (e.g. ["Schedule callback for Monday"]).
- "sentiment": One of "positive", "neutral", or "negative".
- "sentiment_notes": One sentence explaining the caller's tone/mood.
- "lead_info": An object with "name", "email", "phone", "company", "interest_level" (high/medium/low/unknown), and "notes".
- "call_outcome": One of "resolved", "follow_up_needed", "escalated", "information_provided", or "other".
- "topics_discussed": An array of short topic labels (e.g. ["Account balance", "Payment options"]).

TRANSCRIPT:
{transcript_text}"""

        response = await call_mistral(
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.3,
            max_tokens=1000,
            response_format={"type": "json_object"}
        )

        raw = response["message"].strip()
        # Strip markdown code fences if Mistral wraps the JSON
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)

        summary_data = json.loads(raw)
        return summary_data

    except Exception as e:
        logger.error(f"Error generating call summary: {e}")
        return None


@router.post("/vapi")
async def vapi_webhook(payload: VapiWebhookPayload):
    """Handle VAPI webhooks (end-of-call events)"""
    try:
        message = payload.message
        message_type = message.get("type")

        if message_type == "end-of-call-report":
            call = payload.call or {}
            artifact = payload.artifact or {}
            
            call_id = call.get("id")
            assistant_id = call.get("assistantId")

            # Save call record - try multiple duration field names (VAPI uses different names)
            duration = (
                call.get("durationSeconds") or
                call.get("duration") or
                artifact.get("durationSeconds") or
                artifact.get("duration") or
                0
            )
            # If duration seems to be in milliseconds (> 10000), convert to seconds
            if duration > 10000:
                duration = duration // 1000

            # Fallback: calculate from timestamps if no duration available
            if not duration and call.get("startedAt") and call.get("endedAt"):
                try:
                    from datetime import datetime
                    started = datetime.fromisoformat(call["startedAt"].replace("Z", "+00:00"))
                    ended = datetime.fromisoformat(call["endedAt"].replace("Z", "+00:00"))
                    duration = int((ended - started).total_seconds())
                except Exception:
                    pass

            call_data = {
                "id": call_id,
                "assistant_id": assistant_id,
                "phone_number": call.get("customer", {}).get("number"),
                "status": call.get("status"),
                "started_at": call.get("startedAt"),
                "ended_at": call.get("endedAt"),
                "duration_seconds": duration,
            }

            supabase_admin.table("voice_assistant_calls").upsert(
                call_data, on_conflict="id"
            ).execute()

            # Save recording if available
            recording_url = artifact.get("recordingUrl") if artifact else None
            if recording_url:
                existing_rec = supabase_admin.table("voice_assistant_recordings").select(
                    "id"
                ).eq("call_id", call_id).limit(1).execute()
                if not existing_rec.data:
                    supabase_admin.table("voice_assistant_recordings").insert({
                        "call_id": call_id,
                        "recording_url": recording_url
                    }).execute()

            # Save transcript and build transcript text for summary
            transcript_text = ""
            if artifact.get("transcript"):
                transcript = artifact["transcript"]
                for msg in transcript.get("messages", []):
                    role = msg.get("role", "unknown")
                    content = msg.get("message", "")
                    transcript_text += f"{role}: {content}\n"
                    
                    supabase_admin.table("voice_assistant_transcripts").insert({
                        "call_id": call_id,
                        "role": role,
                        "content": content,
                        "timestamp": msg.get("time")
                    }).execute()

            # Save recording
            if artifact.get("recordingUrl"):
                supabase_admin.table("voice_assistant_recordings").insert({
                    "call_id": call_id,
                    "recording_url": artifact["recordingUrl"]
                }).execute()

            # Generate AI summary if we have transcript
            if transcript_text and assistant_id:
                # Get user_id from voice assistant
                assistant_result = supabase_admin.table("voice_assistants").select(
                    "user_id"
                ).eq("id", assistant_id).single().execute()
                
                if assistant_result.data:
                    user_id = assistant_result.data["user_id"]
                    summary = await generate_call_summary(call_id, transcript_text, user_id)
                    
                    if summary:
                        # Save summary to call record
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
                        
                        logger.info(f"Generated summary for call {call_id}")

        return {"success": True}

    except Exception as e:
        logger.error(f"VAPI webhook error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
