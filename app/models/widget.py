"""Widget-related Pydantic models"""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any


class WidgetConfig(BaseModel):
    """Widget configuration response"""
    chatbot_id: str
    name: str
    theme: Dict[str, Any]
    first_message: Optional[str] = None
    placeholder_text: Optional[str] = None
    forms: List[Dict[str, Any]] = []
    faqs: List[Dict[str, Any]] = []
    widget_position: str = "bottom-right"
    primary_color: Optional[str] = None
    secondary_color: Optional[str] = None


class WidgetMessageRequest(BaseModel):
    """Widget message request (public, no auth)"""
    chatbot_id: str
    visitor_id: str
    message: str
    conversation_id: Optional[str] = None


class WidgetMessageResponse(BaseModel):
    """Widget message response"""
    conversation_id: str
    message: str
    actions: Optional[List[Dict[str, Any]]] = None
