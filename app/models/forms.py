"""Form-related Pydantic models"""
from pydantic import BaseModel, Field
from typing import Dict, Any, Optional


class FormSubmitRequest(BaseModel):
    """Form submission request"""
    form_id: str
    submitted_data: Dict[str, Any]
    conversation_id: Optional[str] = None
    visitor_id: Optional[str] = None


class FormSubmitResponse(BaseModel):
    """Form submission response"""
    submission_id: str
    success: bool
    webhook_sent: bool = False
    error: Optional[str] = None
