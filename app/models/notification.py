"""Notification-related Pydantic models"""
from pydantic import BaseModel, EmailStr
from typing import Optional, Dict, Any


class EmailNotification(BaseModel):
    """Basic email notification"""
    to_email: EmailStr
    subject: str
    html_content: str
    from_name: Optional[str] = "Voxtro"


class NotificationRequest(BaseModel):
    """General notification request"""
    user_id: str
    notification_type: str
    data: Dict[str, Any]


class ContactFormRequest(BaseModel):
    """Contact form submission"""
    name: str
    email: EmailStr
    subject: str
    message: str


class TeamInviteRequest(BaseModel):
    """Team invitation email request"""
    email: EmailStr
    team_name: str
    inviter_name: Optional[str] = None
    invite_url: str
