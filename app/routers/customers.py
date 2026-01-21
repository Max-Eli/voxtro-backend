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
    """
    Create customer with auth user and customer portal access
    Optionally link to a specific chatbot/agent for the portal
    """
    try:
        business_owner_id = auth_data["user_id"]  # The business owner creating the customer

        # Create auth user via Supabase Admin API for customer portal login
        auth_result = supabase_admin.auth.admin.create_user({
            "email": customer.email,
            "password": customer.password,
            "email_confirm": True,
            "user_metadata": {
                "is_customer": True,
                "full_name": customer.full_name,
                "created_by_user_id": business_owner_id  # Link back to business owner
            }
        })

        customer_user_id = auth_result.user.id

        # Create customer profile with optional chatbot link
        customer_data = {
            "user_id": customer_user_id,  # Link to auth user for portal login
            "email": customer.email,
            "full_name": customer.full_name,
            "company_name": customer.company_name,
            "created_by_user_id": business_owner_id  # Business owner who created this customer
        }

        # Add chatbot link if provided
        if customer.chatbot_id:
            # Verify the chatbot belongs to the business owner
            chatbot_check = supabase_admin.table("chatbots").select("id").eq(
                "id", customer.chatbot_id
            ).eq("user_id", business_owner_id).single().execute()

            if not chatbot_check.data:
                raise HTTPException(status_code=404, detail="Chatbot not found or unauthorized")

            customer_data["chatbot_id"] = customer.chatbot_id

        customer_result = supabase_admin.table("customers").insert(customer_data).execute()

        customer_id = customer_result.data[0]["id"]

        logger.info(f"Customer created: {customer_id} by user {business_owner_id}" +
                   (f" linked to chatbot {customer.chatbot_id}" if customer.chatbot_id else ""))

        return CustomerCreateResponse(
            customer_id=customer_id,
            user_id=customer_user_id,
            email=customer.email
        )

    except HTTPException:
        raise
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


@router.post("/send-login-link")
async def send_customer_login_link(email: str):
    """Send magic login link to customer"""
    try:
        # Generate magic link via Supabase auth
        result = supabase_admin.auth.admin.generate_link({
            "type": "magiclink",
            "email": email,
            "options": {
                "redirect_to": "https://yourdomain.com/customer-portal"
            }
        })

        # Send email with link (via notifications endpoint)
        from app.models.notification import EmailNotification
        from app.routers.notifications import send_email

        email_data = EmailNotification(
            to_email=email,
            subject="Your Login Link - Voxtro Customer Portal",
            html_content=f"""
                <h2>Access Your Customer Portal</h2>
                <p>Click the link below to log in to your customer portal:</p>
                <p><a href="{result.properties.action_link}">Log In</a></p>
                <p>This link expires in 1 hour.</p>
            """,
            from_name="Voxtro"
        )

        await send_email(email_data)

        return {"success": True, "message": "Login link sent"}

    except Exception as e:
        logger.error(f"Send login link error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/extract-leads")
async def extract_leads_from_conversations(auth_data: Dict = Depends(get_current_user)):
    """Extract leads from customer conversations using AI"""
    try:
        user_id = auth_data["user_id"]

        # Get user's OpenAI API key
        from app.services.ai_service import get_user_openai_key
        openai_api_key = await get_user_openai_key(user_id)

        # Get user's chatbots
        chatbots_result = supabase_admin.table("chatbots").select("id").eq(
            "user_id", user_id
        ).execute()

        chatbot_ids = [bot["id"] for bot in chatbots_result.data]

        if not chatbot_ids:
            return {"success": True, "leads_extracted": 0}

        # Get unprocessed conversations
        conversations_result = supabase_admin.table("conversations").select(
            "id, chatbot_id"
        ).in_("chatbot_id", chatbot_ids).eq("lead_extracted", False).limit(50).execute()

        leads_extracted = 0

        for conv in conversations_result.data:
            # Get conversation messages
            messages_result = supabase_admin.table("messages").select(
                "content, role"
            ).eq("conversation_id", conv["id"]).execute()

            # Use OpenAI to extract lead information with user's API key
            from app.services.ai_service import extract_lead_info

            lead_info = await extract_lead_info(messages_result.data, openai_api_key)

            if lead_info:
                # Save lead
                supabase_admin.table("leads").insert({
                    "conversation_id": conv["id"],
                    "chatbot_id": conv["chatbot_id"],
                    "name": lead_info.get("name"),
                    "email": lead_info.get("email"),
                    "phone": lead_info.get("phone"),
                    "company": lead_info.get("company"),
                    "notes": lead_info.get("notes")
                }).execute()

                leads_extracted += 1

            # Mark conversation as processed
            supabase_admin.table("conversations").update({
                "lead_extracted": True
            }).eq("id", conv["id"]).execute()

        return {"success": True, "leads_extracted": leads_extracted}

    except Exception as e:
        logger.error(f"Extract leads error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
