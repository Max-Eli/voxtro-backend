"""Chat-related Pydantic models"""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime


class ChatMessageRequest(BaseModel):
    """Request model for sending a chat message"""
    chatbot_id: str = Field(..., description="Chatbot UUID")
    conversation_id: Optional[str] = Field(None, description="Existing conversation ID")
    visitor_id: str = Field(..., description="Visitor/session ID")
    message: str = Field(..., description="User message content")
    preview_mode: bool = Field(False, description="Preview mode flag")


class ChatMessageResponse(BaseModel):
    """Response model for chat messages"""
    conversation_id: str
    message: str
    actions: Optional[List[Dict[str, Any]]] = None
    form_triggered: Optional[bool] = False
    faqs: Optional[List[Dict[str, Any]]] = None


class ConversationEndRequest(BaseModel):
    """Request to detect and handle conversation end"""
    conversation_id: str
    last_message_time: datetime


class ExtractParametersRequest(BaseModel):
    """Request to extract custom parameters from conversation"""
    conversation_id: str
    chatbot_id: str


class WebsiteCrawlRequest(BaseModel):
    """Request to crawl a website for chatbot knowledge"""
    chatbot_id: str
    url: str
    max_pages: int = Field(10, ge=1, le=100)


class WebsiteCrawlResponse(BaseModel):
    """Response from website crawling"""
    success: bool
    pages_crawled: int
    content_extracted: int
    error: Optional[str] = None
