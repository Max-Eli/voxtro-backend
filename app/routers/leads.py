"""Lead extraction endpoints"""
from fastapi import APIRouter, HTTPException, Depends
from typing import Dict
import logging

from app.middleware.auth import get_current_user
from app.database import supabase_admin

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/extract")
async def extract_leads(conversation_id: str, auth_data: Dict = Depends(get_current_user)):
    """Extract leads from conversation"""
    try:
        # Get conversation messages
        messages_result = supabase_admin.table("messages").select(
            "content, role"
        ).eq("conversation_id", conversation_id).execute()

        # Simple lead extraction logic (you'd use OpenAI here for real extraction)
        # For now, just mark as extracted

        return {"success": True, "leads_found": 0}

    except Exception as e:
        logger.error(f"Lead extraction error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
