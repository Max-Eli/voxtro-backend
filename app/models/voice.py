"""Voice assistant related Pydantic models"""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime


class VoiceAssistantSyncRequest(BaseModel):
    """Request to sync voice assistants from VAPI"""
    org_id: Optional[str] = None


class VoiceAssistantSyncResponse(BaseModel):
    """Response from syncing voice assistants"""
    count: int
    assistants: List[Dict[str, Any]]


class VoiceAssistantUpdate(BaseModel):
    """Update voice assistant configuration"""
    name: Optional[str] = None
    first_message: Optional[str] = None
    voice_id: Optional[str] = None
    model: Optional[str] = None
    system_prompt: Optional[str] = None
    temperature: Optional[float] = Field(None, ge=0, le=2)


class VapiWebhookPayload(BaseModel):
    """VAPI webhook payload"""
    message: Dict[str, Any]
    call: Optional[Dict[str, Any]] = None
    artifact: Optional[Dict[str, Any]] = None


class VoiceConnectionValidation(BaseModel):
    """Validate voice connection"""
    api_key: str
    org_id: Optional[str] = None


class FetchVapiCallsRequest(BaseModel):
    """Request to fetch calls from VAPI for a specific assistant"""
    assistant_id: str


class FetchVapiCallsResponse(BaseModel):
    """Response from fetching VAPI calls"""
    success: bool
    total_from_vapi: int
    synced_count: int
    assistant_name: Optional[str] = None
    # Debug info
    total_all_calls: Optional[int] = None
    assistant_ids_in_vapi: Optional[List[str]] = None
