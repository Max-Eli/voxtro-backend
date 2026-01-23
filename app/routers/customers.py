"""Customer management endpoints"""
from fastapi import APIRouter, HTTPException, Depends
from typing import Dict
import logging

from app.models.customer import CustomerCreate, CustomerCreateResponse, SupportTicketCreate
from app.middleware.auth import get_current_user, get_current_customer
from app.database import supabase_admin
from app.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter()
settings = get_settings()


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

        # Create customer profile
        customer_data = {
            "user_id": customer_user_id,  # Link to auth user for portal login
            "email": customer.email,
            "full_name": customer.full_name,
            "company_name": customer.company_name,
            "created_by_user_id": business_owner_id  # Business owner who created this customer
        }

        customer_result = supabase_admin.table("customers").insert(customer_data).execute()
        customer_id = customer_result.data[0]["id"]

        # Create chatbot assignment if provided (uses separate assignments table)
        if customer.chatbot_id:
            # Verify the chatbot belongs to the business owner
            chatbot_check = supabase_admin.table("chatbots").select("id").eq(
                "id", customer.chatbot_id
            ).eq("user_id", business_owner_id).single().execute()

            if chatbot_check.data:
                # Create assignment in customer_chatbot_assignments table
                supabase_admin.table("customer_chatbot_assignments").insert({
                    "customer_id": customer_id,
                    "chatbot_id": customer.chatbot_id,
                    "assigned_by": business_owner_id
                }).execute()

        logger.info(f"Customer created: {customer_id} by user {business_owner_id}" +
                   (f" with chatbot assignment {customer.chatbot_id}" if customer.chatbot_id else ""))

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
                "redirect_to": f"{settings.frontend_url}/customer-portal"
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


# ============== CUSTOMER PORTAL ENDPOINTS ==============
# These endpoints are for end customers to access their portal

@router.get("/portal/me")
async def get_customer_profile(auth_data: Dict = Depends(get_current_customer)):
    """Get current customer's profile (CUSTOMER PORTAL)"""
    try:
        user_id = auth_data["user_id"]

        # Get customer profile
        customer_result = supabase_admin.table("customers").select(
            "*, chatbot_id"
        ).eq("user_id", user_id).single().execute()

        if not customer_result.data:
            raise HTTPException(status_code=404, detail="Customer profile not found")

        return customer_result.data

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get customer profile error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/portal/agents")
async def get_customer_agents(auth_data: Dict = Depends(get_current_customer)):
    """
    Get agents/chatbots linked to this customer (CUSTOMER PORTAL)
    Returns chatbots, voice assistants, and whatsapp agents the customer has access to
    """
    try:
        user_id = auth_data["user_id"]

        # Get customer profile to find linked chatbot and business owner
        customer_result = supabase_admin.table("customers").select(
            "id, chatbot_id, created_by_user_id"
        ).eq("user_id", user_id).single().execute()

        if not customer_result.data:
            raise HTTPException(status_code=404, detail="Customer profile not found")

        customer = customer_result.data
        business_owner_id = customer.get("created_by_user_id")

        agents = {
            "chatbots": [],
            "voice_assistants": [],
            "whatsapp_agents": []
        }

        # If customer is linked to a specific chatbot, return just that one
        if customer.get("chatbot_id"):
            chatbot_result = supabase_admin.table("chatbots").select(
                "id, name, avatar_url, first_message, is_active"
            ).eq("id", customer["chatbot_id"]).eq("is_active", True).execute()
            agents["chatbots"] = chatbot_result.data or []
        elif business_owner_id:
            # Otherwise, get all active chatbots from the business owner
            chatbots_result = supabase_admin.table("chatbots").select(
                "id, name, avatar_url, first_message, is_active"
            ).eq("user_id", business_owner_id).eq("is_active", True).execute()
            agents["chatbots"] = chatbots_result.data or []

        # Get voice assistants from business owner
        if business_owner_id:
            voice_result = supabase_admin.table("voice_assistants").select(
                "id, name, first_message, phone_number"
            ).eq("user_id", business_owner_id).execute()
            agents["voice_assistants"] = voice_result.data or []

            # Get whatsapp agents from business owner
            whatsapp_result = supabase_admin.table("whatsapp_agents").select(
                "id, name, status"
            ).eq("user_id", business_owner_id).eq("status", "active").execute()
            agents["whatsapp_agents"] = whatsapp_result.data or []

        return agents

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get customer agents error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/portal/conversations")
async def get_customer_conversations(auth_data: Dict = Depends(get_current_customer)):
    """Get customer's conversation history (CUSTOMER PORTAL)"""
    try:
        user_id = auth_data["user_id"]

        # Get customer profile
        customer_result = supabase_admin.table("customers").select(
            "id, chatbot_id, created_by_user_id"
        ).eq("user_id", user_id).single().execute()

        if not customer_result.data:
            raise HTTPException(status_code=404, detail="Customer profile not found")

        customer = customer_result.data

        # Get conversations - if linked to specific chatbot, filter by that
        query = supabase_admin.table("conversations").select(
            "id, chatbot_id, status, created_at, updated_at"
        ).eq("visitor_id", user_id)  # Assuming visitor_id is set to user_id for logged-in customers

        if customer.get("chatbot_id"):
            query = query.eq("chatbot_id", customer["chatbot_id"])

        conversations_result = query.order("updated_at", desc=True).limit(50).execute()

        return {"conversations": conversations_result.data or []}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get customer conversations error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/portal/conversations/{conversation_id}/messages")
async def get_conversation_messages(
    conversation_id: str,
    auth_data: Dict = Depends(get_current_customer)
):
    """Get messages for a specific conversation (CUSTOMER PORTAL)"""
    try:
        user_id = auth_data["user_id"]

        # Verify the conversation belongs to this customer
        conv_result = supabase_admin.table("conversations").select(
            "id, visitor_id"
        ).eq("id", conversation_id).single().execute()

        if not conv_result.data:
            raise HTTPException(status_code=404, detail="Conversation not found")

        if conv_result.data.get("visitor_id") != user_id:
            raise HTTPException(status_code=403, detail="Access denied")

        # Get messages
        messages_result = supabase_admin.table("messages").select(
            "id, role, content, created_at"
        ).eq("conversation_id", conversation_id).order("created_at", desc=False).execute()

        return {"messages": messages_result.data or []}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get conversation messages error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/portal/tickets")
async def get_customer_tickets(auth_data: Dict = Depends(get_current_customer)):
    """Get customer's support tickets (CUSTOMER PORTAL)"""
    try:
        user_id = auth_data["user_id"]

        # Get customer ID
        customer_result = supabase_admin.table("customers").select("id").eq(
            "user_id", user_id
        ).single().execute()

        if not customer_result.data:
            raise HTTPException(status_code=404, detail="Customer profile not found")

        customer_id = customer_result.data["id"]

        # Get tickets
        tickets_result = supabase_admin.table("support_tickets").select(
            "id, subject, description, status, priority, created_at, updated_at"
        ).eq("customer_id", customer_id).order("created_at", desc=True).execute()

        return {"tickets": tickets_result.data or []}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get customer tickets error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/portal/tickets")
async def create_customer_ticket(
    ticket: SupportTicketCreate,
    auth_data: Dict = Depends(get_current_customer)
):
    """Create a support ticket as a customer (CUSTOMER PORTAL)"""
    try:
        user_id = auth_data["user_id"]

        # Get customer profile
        customer_result = supabase_admin.table("customers").select(
            "id, created_by_user_id"
        ).eq("user_id", user_id).single().execute()

        if not customer_result.data:
            raise HTTPException(status_code=404, detail="Customer profile not found")

        customer = customer_result.data

        # Create ticket linked to the business owner
        ticket_result = supabase_admin.table("support_tickets").insert({
            "user_id": customer["created_by_user_id"],  # Business owner sees the ticket
            "customer_id": customer["id"],
            "subject": ticket.subject,
            "description": ticket.description,
            "priority": ticket.priority,
            "status": "open"
        }).execute()

        return {"success": True, "ticket_id": ticket_result.data[0]["id"]}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Create customer ticket error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/portal/leads")
async def get_customer_leads(auth_data: Dict = Depends(get_current_customer)):
    """Get leads for customer's assigned agents (CUSTOMER PORTAL)"""
    try:
        user_id = auth_data["user_id"]

        # Get customer profile
        customer_result = supabase_admin.table("customers").select(
            "id"
        ).eq("user_id", user_id).single().execute()

        if not customer_result.data:
            raise HTTPException(status_code=404, detail="Customer profile not found")

        customer_id = customer_result.data["id"]
        all_leads = []

        # Get chatbot assignments and their leads
        chatbot_assignments = supabase_admin.table("customer_chatbot_assignments").select(
            "chatbot_id, chatbots(name)"
        ).eq("customer_id", customer_id).execute()

        if chatbot_assignments.data:
            chatbot_ids = [a["chatbot_id"] for a in chatbot_assignments.data]
            chatbot_names = {a["chatbot_id"]: a.get("chatbots", {}).get("name", "Unknown") for a in chatbot_assignments.data}

            # Get leads for these chatbots
            chatbot_leads = supabase_admin.table("leads").select(
                "id, conversation_id, name, email, phone, company, notes, created_at, chatbot_id"
            ).in_("chatbot_id", chatbot_ids).order("created_at", desc=True).execute()

            if chatbot_leads.data:
                for lead in chatbot_leads.data:
                    all_leads.append({
                        "id": lead["id"],
                        "source_type": "chatbot",
                        "source_id": lead["chatbot_id"],
                        "source_name": chatbot_names.get(lead["chatbot_id"], "Unknown"),
                        "conversation_id": lead["conversation_id"],
                        "name": lead.get("name"),
                        "email": lead.get("email"),
                        "phone_number": lead.get("phone"),
                        "additional_data": {"company": lead.get("company"), "notes": lead.get("notes")},
                        "extracted_at": lead["created_at"]
                    })

        # Get voice assistant assignments and their leads (if table exists)
        try:
            voice_assignments = supabase_admin.table("customer_assistant_assignments").select(
                "assistant_id, voice_assistants(name)"
            ).eq("customer_id", customer_id).execute()

            if voice_assignments.data:
                assistant_ids = [a["assistant_id"] for a in voice_assignments.data]
                assistant_names = {a["assistant_id"]: a.get("voice_assistants", {}).get("name", "Unknown") for a in voice_assignments.data}

                # Check if leads table has source_type column for voice leads
                voice_leads = supabase_admin.table("leads").select(
                    "id, conversation_id, name, email, phone, company, notes, created_at, source_id, source_type"
                ).eq("source_type", "voice").in_("source_id", assistant_ids).order("created_at", desc=True).execute()

                if voice_leads.data:
                    for lead in voice_leads.data:
                        all_leads.append({
                            "id": lead["id"],
                            "source_type": "voice",
                            "source_id": lead["source_id"],
                            "source_name": assistant_names.get(lead["source_id"], "Unknown"),
                            "conversation_id": lead.get("conversation_id"),
                            "name": lead.get("name"),
                            "email": lead.get("email"),
                            "phone_number": lead.get("phone"),
                            "additional_data": {"company": lead.get("company"), "notes": lead.get("notes")},
                            "extracted_at": lead["created_at"]
                        })
        except Exception as e:
            logger.debug(f"Voice assistant leads not available: {e}")

        # Get whatsapp agent assignments and their leads (if table exists)
        try:
            whatsapp_assignments = supabase_admin.table("customer_whatsapp_agent_assignments").select(
                "agent_id, whatsapp_agents(name)"
            ).eq("customer_id", customer_id).execute()

            if whatsapp_assignments.data:
                agent_ids = [a["agent_id"] for a in whatsapp_assignments.data]
                agent_names = {a["agent_id"]: a.get("whatsapp_agents", {}).get("name", "Unknown") for a in whatsapp_assignments.data}

                whatsapp_leads = supabase_admin.table("leads").select(
                    "id, conversation_id, name, email, phone, company, notes, created_at, source_id, source_type"
                ).eq("source_type", "whatsapp").in_("source_id", agent_ids).order("created_at", desc=True).execute()

                if whatsapp_leads.data:
                    for lead in whatsapp_leads.data:
                        all_leads.append({
                            "id": lead["id"],
                            "source_type": "whatsapp",
                            "source_id": lead["source_id"],
                            "source_name": agent_names.get(lead["source_id"], "Unknown"),
                            "conversation_id": lead.get("conversation_id"),
                            "name": lead.get("name"),
                            "email": lead.get("email"),
                            "phone_number": lead.get("phone"),
                            "additional_data": {"company": lead.get("company"), "notes": lead.get("notes")},
                            "extracted_at": lead["created_at"]
                        })
        except Exception as e:
            logger.debug(f"WhatsApp agent leads not available: {e}")

        # Sort all leads by date descending
        all_leads.sort(key=lambda x: x["extracted_at"], reverse=True)

        return {"leads": all_leads}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get customer leads error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
