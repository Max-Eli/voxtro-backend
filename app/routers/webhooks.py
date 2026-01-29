"""Webhooks from external services"""
from fastapi import APIRouter, HTTPException
import logging
from typing import Dict, Any, Optional

from app.models.voice import VapiWebhookPayload
from app.database import supabase_admin
from app.services.ai_service import call_openai, get_user_openai_key

logger = logging.getLogger(__name__)
router = APIRouter()


async def generate_call_summary(call_id: str, transcript_text: str, user_id: str) -> Optional[Dict[str, Any]]:
    """
    Generate AI summary of a voice call transcript
    
    Args:
        call_id: The call ID
        transcript_text: Full transcript text
        user_id: User ID to get OpenAI key
        
    Returns:
        Summary dict with summary, key_points, action_items, sentiment, lead_info
    """
    try:
        # Get user's OpenAI API key
        openai_api_key = await get_user_openai_key(user_id, allow_fallback=True)
        
        summary_prompt = f"""Analyze the following voice call transcript and provide a comprehensive summary.

TRANSCRIPT:
{transcript_text}

Provide your analysis in the following JSON format:
{{
    "summary": "Brief 2-3 sentence summary of the call",
    "key_points": ["Key point 1", "Key point 2", ...],
    "action_items": ["Action item 1", "Action item 2", ...],
    "sentiment": "positive/neutral/negative",
    "sentiment_notes": "Brief explanation of caller sentiment",
    "lead_info": {{
        "name": "Caller name if mentioned",
        "email": "Email if mentioned",
        "phone": "Phone if mentioned",
        "company": "Company if mentioned",
        "interest_level": "high/medium/low/unknown",
        "notes": "Any relevant notes about the potential lead"
    }},
    "call_outcome": "resolved/follow_up_needed/escalated/information_provided/other",
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
        
        import json
        summary_data = json.loads(response["message"])
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

            # Save call record
            call_data = {
                "id": call_id,
                "assistant_id": assistant_id,
                "phone_number": call.get("customer", {}).get("number"),
                "status": call.get("status"),
                "started_at": call.get("startedAt"),
                "ended_at": call.get("endedAt"),
                "duration_seconds": call.get("duration") or 0,
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
