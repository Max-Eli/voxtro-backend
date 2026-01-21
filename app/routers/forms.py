"""Form handling endpoints"""
from fastapi import APIRouter, HTTPException
import logging

from app.models.forms import FormSubmitRequest, FormSubmitResponse
from app.database import supabase_admin

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/submit", response_model=FormSubmitResponse)
async def submit_form(form: FormSubmitRequest):
    """Handle form submission (PUBLIC endpoint)"""
    try:
        # Save submission
        submission_result = supabase_admin.table("form_submissions").insert({
            "form_id": form.form_id,
            "conversation_id": form.conversation_id,
            "visitor_id": form.visitor_id,
            "submitted_data": form.submitted_data
        }).execute()

        submission_id = submission_result.data[0]["id"]

        # Get form configuration to check for webhook
        form_result = supabase_admin.table("chatbot_forms").select("*").eq(
            "id", form.form_id
        ).single().execute()

        webhook_sent = False
        if form_result.data and form_result.data.get("webhook_url"):
            # Send to webhook (implement webhook call)
            webhook_sent = True

        return FormSubmitResponse(
            submission_id=submission_id,
            success=True,
            webhook_sent=webhook_sent
        )

    except Exception as e:
        logger.error(f"Form submission error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
