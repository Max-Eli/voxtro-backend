"""Customer management endpoints"""
from fastapi import APIRouter, HTTPException, Depends
from typing import Dict
import logging

from app.models.customer import CustomerCreate, CustomerCreateResponse, SupportTicketCreate
from app.middleware.auth import get_current_user
from app.database import supabase_admin

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("", response_model=CustomerCreateResponse)
async def create_customer(customer: CustomerCreate, auth_data: Dict = Depends(get_current_user)):
    """Create customer with auth user"""
    try:
        # Create auth user via Supabase Admin API
        auth_result = supabase_admin.auth.admin.create_user({
            "email": customer.email,
            "password": customer.password,
            "email_confirm": True,
            "user_metadata": {"is_customer": True, "full_name": customer.full_name}
        })

        user_id = auth_result.user.id

        # Create customer profile
        customer_result = supabase_admin.table("customers").insert({
            "email": customer.email,
            "full_name": customer.full_name,
            "company_name": customer.company_name
        }).execute()

        customer_id = customer_result.data[0]["id"]

        return CustomerCreateResponse(
            customer_id=customer_id,
            user_id=user_id,
            email=customer.email
        )

    except Exception as e:
        logger.error(f"Customer creation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/tickets")
async def create_support_ticket(ticket: SupportTicketCreate, auth_data: Dict = Depends(get_current_user)):
    """Create support ticket"""
    try:
        user_id = auth_data["user_id"]

        ticket_result = supabase_admin.table("support_tickets").insert({
            "user_id": user_id,
            "customer_id": ticket.customer_id,
            "subject": ticket.subject,
            "description": ticket.description,
            "priority": ticket.priority,
            "status": "open"
        }).execute()

        return {"success": True, "ticket_id": ticket_result.data[0]["id"]}

    except Exception as e:
        logger.error(f"Ticket creation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
