"""Customer management Pydantic models"""
from pydantic import BaseModel, EmailStr, Field
from typing import Optional


class CustomerCreate(BaseModel):
    """Create a new customer with auth"""
    email: EmailStr
    password: str = Field(..., min_length=6)
    full_name: str
    company_name: Optional[str] = None
    chatbot_id: Optional[str] = None  # Link customer to specific chatbot/agent


class CustomerCreateResponse(BaseModel):
    """Response after creating customer"""
    customer_id: str
    user_id: str
    email: str


class CustomerLoginLinkRequest(BaseModel):
    """Request to send customer login link"""
    customer_id: str


class SupportTicketCreate(BaseModel):
    """Create a support ticket"""
    subject: str
    description: str
    priority: str = Field("medium", pattern="^(low|medium|high|urgent)$")
    customer_id: Optional[str] = None


class SupportTicketReply(BaseModel):
    """Reply to a support ticket"""
    ticket_id: str
    message: str
    is_admin: bool = False
