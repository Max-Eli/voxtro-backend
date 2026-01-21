"""WhatsApp agent related Pydantic models"""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any


class WhatsAppAgentSyncRequest(BaseModel):
    """Request to sync WhatsApp agents from ElevenLabs"""
    pass


class WhatsAppAgentSyncResponse(BaseModel):
    """Response from syncing WhatsApp agents"""
    count: int
    agents: List[Dict[str, Any]]


class WhatsAppAgentUpdate(BaseModel):
    """Update WhatsApp agent configuration"""
    name: Optional[str] = None
    system_prompt: Optional[str] = None
    first_message: Optional[str] = None
    language: Optional[str] = None
    voice_id: Optional[str] = None
    model_id: Optional[str] = None
    temperature: Optional[float] = Field(None, ge=0, le=2)


class ElevenLabsConnectionValidation(BaseModel):
    """Validate ElevenLabs connection"""
    api_key: str
