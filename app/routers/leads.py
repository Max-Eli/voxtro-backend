"""Lead extraction endpoints"""
from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Optional
import logging

from app.middleware.auth import get_current_user
from app.database import supabase_admin
from app.services.ai_service import get_user_openai_key, extract_lead_info

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/extract")
async def extract_leads(conversation_id: str, auth_data: Dict = Depends(get_current_user)):
    """Extract leads from a specific conversation using AI"""
    try:
        user_id = auth_data["user_id"]

        # Verify conversation belongs to user's chatbot
        conv_result = supabase_admin.table("conversations").select(
            "id, chatbot_id"
        ).eq("id", conversation_id).single().execute()

        if not conv_result.data:
            raise HTTPException(status_code=404, detail="Conversation not found")

        chatbot_id = conv_result.data["chatbot_id"]

        # Verify chatbot belongs to user and get name
        chatbot_result = supabase_admin.table("chatbots").select("id, name").eq(
            "id", chatbot_id
        ).eq("user_id", user_id).single().execute()

        if not chatbot_result.data:
            raise HTTPException(status_code=403, detail="Access denied")

        chatbot_name = chatbot_result.data.get("name")

        # Get user's OpenAI API key
        openai_api_key = await get_user_openai_key(user_id)

        # Get conversation messages
        messages_result = supabase_admin.table("messages").select(
            "content, role"
        ).eq("conversation_id", conversation_id).order("created_at").execute()

        if not messages_result.data:
            return {"success": True, "leads_found": 0, "message": "No messages in conversation"}

        # Use AI to extract lead information
        lead_info = await extract_lead_info(messages_result.data, openai_api_key)

        if not lead_info:
            return {"success": True, "leads_found": 0, "message": "No lead information found"}

        # Save lead to database with correct schema
        lead_result = supabase_admin.table("leads").insert({
            "conversation_id": conversation_id,
            "source_id": chatbot_id,
            "source_type": "chatbot",
            "source_name": chatbot_name,
            "user_id": user_id,
            "name": lead_info.get("name"),
            "email": lead_info.get("email"),
            "phone_number": lead_info.get("phone"),
            "additional_data": {
                "company": lead_info.get("company"),
                "notes": lead_info.get("notes")
            }
        }).execute()

        # Mark conversation as lead extracted
        supabase_admin.table("conversations").update({
            "lead_extracted": True
        }).eq("id", conversation_id).execute()

        return {
            "success": True,
            "leads_found": 1,
            "lead_id": lead_result.data[0]["id"],
            "lead_info": lead_info
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Lead extraction error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/extract-batch")
async def extract_leads_batch(
    chatbot_id: Optional[str] = None,
    limit: int = 50,
    auth_data: Dict = Depends(get_current_user)
):
    """Extract leads from multiple unprocessed conversations"""
    try:
        user_id = auth_data["user_id"]

        # Get user's OpenAI API key
        openai_api_key = await get_user_openai_key(user_id)

        # Build query for user's chatbots with names
        if chatbot_id:
            # Verify chatbot belongs to user and get name
            chatbot_check = supabase_admin.table("chatbots").select("id, name").eq(
                "id", chatbot_id
            ).eq("user_id", user_id).single().execute()

            if not chatbot_check.data:
                raise HTTPException(status_code=403, detail="Chatbot not found or access denied")

            chatbot_ids = [chatbot_id]
            chatbot_names = {chatbot_id: chatbot_check.data.get("name")}
        else:
            # Get all user's chatbots with names
            chatbots_result = supabase_admin.table("chatbots").select("id, name").eq(
                "user_id", user_id
            ).execute()
            chatbot_ids = [bot["id"] for bot in (chatbots_result.data or [])]
            chatbot_names = {bot["id"]: bot["name"] for bot in (chatbots_result.data or [])}

        if not chatbot_ids:
            return {"success": True, "leads_extracted": 0, "message": "No chatbots found"}

        # Get unprocessed conversations
        conversations_result = supabase_admin.table("conversations").select(
            "id, chatbot_id"
        ).in_("chatbot_id", chatbot_ids).eq("lead_extracted", False).limit(limit).execute()

        leads_extracted = 0
        leads = []

        for conv in (conversations_result.data or []):
            # Get conversation messages
            messages_result = supabase_admin.table("messages").select(
                "content, role"
            ).eq("conversation_id", conv["id"]).order("created_at").execute()

            if not messages_result.data:
                continue

            # Use AI to extract lead information
            lead_info = await extract_lead_info(messages_result.data, openai_api_key)

            if lead_info:
                # Save lead with correct schema
                lead_result = supabase_admin.table("leads").insert({
                    "conversation_id": conv["id"],
                    "source_id": conv["chatbot_id"],
                    "source_type": "chatbot",
                    "source_name": chatbot_names.get(conv["chatbot_id"]),
                    "user_id": user_id,
                    "name": lead_info.get("name"),
                    "email": lead_info.get("email"),
                    "phone_number": lead_info.get("phone"),
                    "additional_data": {
                        "company": lead_info.get("company"),
                        "notes": lead_info.get("notes")
                    }
                }).execute()

                leads_extracted += 1
                leads.append({
                    "lead_id": lead_result.data[0]["id"],
                    "conversation_id": conv["id"],
                    "lead_info": lead_info
                })

            # Mark conversation as processed
            supabase_admin.table("conversations").update({
                "lead_extracted": True
            }).eq("id", conv["id"]).execute()

        return {
            "success": True,
            "leads_extracted": leads_extracted,
            "conversations_processed": len(conversations_result.data or []),
            "leads": leads
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Batch lead extraction error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("")
async def get_leads(
    chatbot_id: Optional[str] = None,
    limit: int = 100,
    auth_data: Dict = Depends(get_current_user)
):
    """Get extracted leads for user's chatbots"""
    try:
        user_id = auth_data["user_id"]

        # Get user's chatbots
        if chatbot_id:
            chatbot_check = supabase_admin.table("chatbots").select("id").eq(
                "id", chatbot_id
            ).eq("user_id", user_id).single().execute()

            if not chatbot_check.data:
                raise HTTPException(status_code=403, detail="Chatbot not found or access denied")

            chatbot_ids = [chatbot_id]
        else:
            chatbots_result = supabase_admin.table("chatbots").select("id").eq(
                "user_id", user_id
            ).execute()
            chatbot_ids = [bot["id"] for bot in (chatbots_result.data or [])]

        if not chatbot_ids:
            return {"leads": []}

        # Get leads with correct column names
        leads_result = supabase_admin.table("leads").select(
            "id, conversation_id, source_id, source_type, source_name, name, email, phone_number, additional_data, extracted_at"
        ).in_("source_id", chatbot_ids).eq("source_type", "chatbot").order("extracted_at", desc=True).limit(limit).execute()

        return {"leads": leads_result.data or []}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get leads error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
