"""Customer management endpoints"""
from fastapi import APIRouter, HTTPException, Depends
from typing import Dict
import logging
import uuid

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

        # Get user's chatbots with names
        chatbots_result = supabase_admin.table("chatbots").select("id, name").eq(
            "user_id", user_id
        ).execute()

        chatbot_ids = [bot["id"] for bot in chatbots_result.data]
        chatbot_names = {bot["id"]: bot["name"] for bot in chatbots_result.data}

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
                # Save lead with correct schema
                supabase_admin.table("leads").insert({
                    "conversation_id": conv["id"],
                    "source_id": conv["chatbot_id"],
                    "source_type": "chatbot",
                    "source_name": chatbot_names.get(conv["chatbot_id"]),
                    "user_id": user_id,
                    "name": lead_info.get("name"),
                    "email": lead_info.get("email"),
                    "phone_number": lead_info.get("phone"),
                    "additional_data": {
                        "company": lead_info.get("company"),
                        "notes": lead_info.get("notes")
                    }
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

            # Get chatbot leads from conversations.lead_info (populated by AI summary)
            chatbot_convos = supabase_admin.table("conversations").select(
                "id, chatbot_id, lead_info, created_at"
            ).in_("chatbot_id", chatbot_ids).not_.is_("lead_info", "null").order("created_at", desc=True).execute()

            if chatbot_convos.data:
                for conv in chatbot_convos.data:
                    lead_info = conv.get("lead_info") or {}
                    if lead_info.get("name") or lead_info.get("email") or lead_info.get("phone"):
                        all_leads.append({
                            "id": conv["id"],
                            "source_type": "chatbot",
                            "source_id": conv["chatbot_id"],
                            "source_name": chatbot_names.get(conv["chatbot_id"], "Unknown"),
                            "conversation_id": conv["id"],
                            "name": lead_info.get("name"),
                            "email": lead_info.get("email"),
                            "phone_number": lead_info.get("phone"),
                            "additional_data": {"company": lead_info.get("company"), "interest_level": lead_info.get("interest_level")},
                            "extracted_at": conv["created_at"]
                        })

        # Get voice assistant leads from voice_assistant_calls.lead_info
        try:
            voice_assignments = supabase_admin.table("customer_assistant_assignments").select(
                "assistant_id, voice_assistants(name)"
            ).eq("customer_id", customer_id).execute()

            if voice_assignments.data:
                assistant_ids = [a["assistant_id"] for a in voice_assignments.data]
                assistant_names = {a["assistant_id"]: a.get("voice_assistants", {}).get("name", "Unknown") for a in voice_assignments.data}

                # Voice leads are stored in voice_assistant_calls.lead_info JSON column
                voice_calls = supabase_admin.table("voice_assistant_calls").select(
                    "id, assistant_id, lead_info, started_at"
                ).in_("assistant_id", assistant_ids).not_.is_("lead_info", "null").order("started_at", desc=True).execute()

                if voice_calls.data:
                    for call in voice_calls.data:
                        lead_info = call.get("lead_info") or {}
                        if lead_info.get("name") or lead_info.get("email") or lead_info.get("phone"):
                            all_leads.append({
                                "id": call["id"],
                                "source_type": "voice",
                                "source_id": call["assistant_id"],
                                "source_name": assistant_names.get(call["assistant_id"], "Unknown"),
                                "conversation_id": call["id"],
                                "name": lead_info.get("name"),
                                "email": lead_info.get("email"),
                                "phone_number": lead_info.get("phone"),
                                "additional_data": {"company": lead_info.get("company"), "interest_level": lead_info.get("interest_level")},
                                "extracted_at": call["started_at"]
                            })
        except Exception as e:
            logger.debug(f"Voice assistant leads not available: {e}")

        # Get whatsapp agent leads from whatsapp_conversations.lead_info
        try:
            whatsapp_assignments = supabase_admin.table("customer_whatsapp_agent_assignments").select(
                "agent_id, whatsapp_agents(name)"
            ).eq("customer_id", customer_id).execute()

            if whatsapp_assignments.data:
                agent_ids = [a["agent_id"] for a in whatsapp_assignments.data]
                agent_names = {a["agent_id"]: a.get("whatsapp_agents", {}).get("name", "Unknown") for a in whatsapp_assignments.data}

                # WhatsApp leads are stored in whatsapp_conversations.lead_info JSON column
                wa_convos = supabase_admin.table("whatsapp_conversations").select(
                    "id, agent_id, lead_info, created_at"
                ).in_("agent_id", agent_ids).not_.is_("lead_info", "null").order("created_at", desc=True).execute()

                if wa_convos.data:
                    for conv in wa_convos.data:
                        lead_info = conv.get("lead_info") or {}
                        if lead_info.get("name") or lead_info.get("email") or lead_info.get("phone"):
                            all_leads.append({
                                "id": conv["id"],
                                "source_type": "whatsapp",
                                "source_id": conv["agent_id"],
                                "source_name": agent_names.get(conv["agent_id"], "Unknown"),
                                "conversation_id": conv["id"],
                                "name": lead_info.get("name"),
                                "email": lead_info.get("email"),
                                "phone_number": lead_info.get("phone"),
                                "additional_data": {"company": lead_info.get("company"), "interest_level": lead_info.get("interest_level")},
                                "extracted_at": conv["created_at"]
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


@router.get("/portal/analytics")
async def get_customer_analytics(auth_data: Dict = Depends(get_current_customer)):
    """Get comprehensive analytics for customer's assigned agents (CUSTOMER PORTAL)"""
    try:
        user_id = auth_data["user_id"]

        # Get customer profile
        customer_result = supabase_admin.table("customers").select(
            "id"
        ).eq("user_id", user_id).single().execute()

        if not customer_result.data:
            raise HTTPException(status_code=404, detail="Customer profile not found")

        customer_id = customer_result.data["id"]

        # Initialize response structure
        response = {
            "chatbots": {
                "assigned": [],
                "total_conversations": 0,
                "total_messages": 0,
                "avg_messages_per_conversation": 0
            },
            "voice_assistants": {
                "assigned": [],
                "total_calls": 0,
                "total_duration": 0,
                "avg_duration": 0,
                "success_rate": 0
            },
            "whatsapp_agents": {
                "assigned": [],
                "total_conversations": 0,
                "total_messages": 0
            },
            "leads": {
                "recent": [],
                "total_count": 0,
                "conversion_rates": {
                    "chatbot": 0,
                    "voice": 0,
                    "whatsapp": 0,
                    "overall": 0
                }
            },
            "support_tickets": {
                "recent": [],
                "open_count": 0
            }
        }

        # Initialize ID lists
        chatbot_ids = []
        assistant_ids = []
        agent_ids = []

        # ========== CHATBOTS ==========
        chatbot_assignments = supabase_admin.table("customer_chatbot_assignments").select(
            "chatbot_id, chatbots(id, name, description, theme_color)"
        ).eq("customer_id", customer_id).execute()
        if chatbot_assignments.data:
            chatbot_ids = [a["chatbot_id"] for a in chatbot_assignments.data]

            # Get conversation counts
            conversations = supabase_admin.table("conversations").select(
                "id, chatbot_id"
            ).in_("chatbot_id", chatbot_ids).execute()

            conversation_counts = {}
            for conv in (conversations.data or []):
                cid = conv["chatbot_id"]
                conversation_counts[cid] = conversation_counts.get(cid, 0) + 1

            # Get message counts
            conv_ids = [c["id"] for c in (conversations.data or [])]
            message_counts = {}
            if conv_ids:
                messages = supabase_admin.table("messages").select(
                    "conversation_id"
                ).in_("conversation_id", conv_ids).execute()

                # Map conversation_id to chatbot_id
                conv_to_chatbot = {c["id"]: c["chatbot_id"] for c in (conversations.data or [])}
                for msg in (messages.data or []):
                    chatbot_id = conv_to_chatbot.get(msg["conversation_id"])
                    if chatbot_id:
                        message_counts[chatbot_id] = message_counts.get(chatbot_id, 0) + 1

            # Build chatbot list
            for assignment in chatbot_assignments.data:
                chatbot = assignment.get("chatbots", {})
                cid = chatbot.get("id")
                response["chatbots"]["assigned"].append({
                    "id": cid,
                    "name": chatbot.get("name", "Unknown"),
                    "description": chatbot.get("description"),
                    "theme_color": chatbot.get("theme_color"),
                    "conversation_count": conversation_counts.get(cid, 0),
                    "message_count": message_counts.get(cid, 0)
                })

            total_convs = len(conversations.data or [])
            total_msgs = sum(message_counts.values())
            response["chatbots"]["total_conversations"] = total_convs
            response["chatbots"]["total_messages"] = total_msgs
            response["chatbots"]["avg_messages_per_conversation"] = (
                round(total_msgs / total_convs) if total_convs > 0 else 0
            )

        # ========== VOICE ASSISTANTS ==========
        try:
            voice_assignments = supabase_admin.table("customer_assistant_assignments").select(
                "assistant_id, voice_assistants(id, name, first_message, voice_provider, phone_number)"
            ).eq("customer_id", customer_id).execute()

            if voice_assignments.data:
                assistant_ids = [a["assistant_id"] for a in voice_assignments.data]

                # Get call stats
                calls = supabase_admin.table("voice_assistant_calls").select(
                    "assistant_id, duration_seconds, status"
                ).in_("assistant_id", assistant_ids).execute()

                call_counts = {}
                duration_sums = {}
                for call in (calls.data or []):
                    aid = call["assistant_id"]
                    call_counts[aid] = call_counts.get(aid, 0) + 1
                    if call.get("duration_seconds"):
                        duration_sums[aid] = duration_sums.get(aid, 0) + call["duration_seconds"]

                for assignment in voice_assignments.data:
                    assistant = assignment.get("voice_assistants", {})
                    aid = assistant.get("id")
                    response["voice_assistants"]["assigned"].append({
                        "id": aid,
                        "name": assistant.get("name", "Unknown"),
                        "first_message": assistant.get("first_message"),
                        "voice_provider": assistant.get("voice_provider"),
                        "phone_number": assistant.get("phone_number"),
                        "call_count": call_counts.get(assignment["assistant_id"], 0),
                        "total_duration": duration_sums.get(assignment["assistant_id"], 0)
                    })

                total_calls = len(calls.data or [])
                total_duration = sum(duration_sums.values())
                successful_calls = len([c for c in (calls.data or []) if c.get("duration_seconds", 0) > 0])
                response["voice_assistants"]["total_calls"] = total_calls
                response["voice_assistants"]["total_duration"] = total_duration
                response["voice_assistants"]["avg_duration"] = (
                    round(total_duration / successful_calls) if successful_calls > 0 else 0
                )
                response["voice_assistants"]["success_rate"] = (
                    round((successful_calls / total_calls) * 100) if total_calls > 0 else 0
                )
        except Exception as e:
            logger.debug(f"Voice assistant data not available: {e}")

        # ========== WHATSAPP AGENTS ==========
        try:
            wa_assignments = supabase_admin.table("customer_whatsapp_agent_assignments").select(
                "agent_id, whatsapp_agents(id, name, phone_number, status)"
            ).eq("customer_id", customer_id).execute()

            if wa_assignments.data:
                agent_ids = [a["agent_id"] for a in wa_assignments.data]

                # Get conversation counts
                wa_convs = supabase_admin.table("whatsapp_conversations").select(
                    "id, agent_id"
                ).in_("agent_id", agent_ids).execute()

                conv_counts = {}
                for conv in (wa_convs.data or []):
                    aid = conv["agent_id"]
                    conv_counts[aid] = conv_counts.get(aid, 0) + 1

                # Get message count
                wa_conv_ids = [c["id"] for c in (wa_convs.data or [])]
                total_wa_msgs = 0
                if wa_conv_ids:
                    wa_msgs = supabase_admin.table("whatsapp_messages").select(
                        "id", count="exact"
                    ).in_("conversation_id", wa_conv_ids).execute()
                    total_wa_msgs = wa_msgs.count or 0

                for assignment in wa_assignments.data:
                    agent = assignment.get("whatsapp_agents", {})
                    response["whatsapp_agents"]["assigned"].append({
                        "id": agent.get("id"),
                        "name": agent.get("name", "Unknown"),
                        "phone_number": agent.get("phone_number"),
                        "status": agent.get("status"),
                        "conversation_count": conv_counts.get(assignment["agent_id"], 0)
                    })

                response["whatsapp_agents"]["total_conversations"] = len(wa_convs.data or [])
                response["whatsapp_agents"]["total_messages"] = total_wa_msgs
        except Exception as e:
            logger.debug(f"WhatsApp agent data not available: {e}")

        # ========== LEADS ==========
        # Leads are stored in the lead_info JSON column of each conversation type
        total_leads = 0
        chatbot_leads_count = 0
        voice_leads_count = 0
        wa_leads_count = 0
        recent_leads_list = []

        # Get chatbot leads from conversations.lead_info
        if chatbot_ids:
            chatbot_leads_result = supabase_admin.table("conversations").select(
                "id, chatbot_id, lead_info, created_at"
            ).in_("chatbot_id", chatbot_ids).not_.is_("lead_info", "null").order("created_at", desc=True).execute()

            if chatbot_leads_result.data:
                for conv in chatbot_leads_result.data:
                    lead_info = conv.get("lead_info") or {}
                    if lead_info.get("name") or lead_info.get("email") or lead_info.get("phone"):
                        chatbot_leads_count += 1
                        if len(recent_leads_list) < 5:
                            recent_leads_list.append({
                                "id": conv["id"],
                                "name": lead_info.get("name"),
                                "email": lead_info.get("email"),
                                "phone_number": lead_info.get("phone"),
                                "source_type": "chatbot",
                                "source_name": chatbot_names.get(conv["chatbot_id"], "Unknown") if 'chatbot_names' in dir() else None,
                                "extracted_at": conv["created_at"]
                            })
            total_leads += chatbot_leads_count

        # Get voice leads from voice_assistant_calls.lead_info
        if assistant_ids:
            voice_leads_result = supabase_admin.table("voice_assistant_calls").select(
                "id, assistant_id, lead_info, started_at"
            ).in_("assistant_id", assistant_ids).not_.is_("lead_info", "null").execute()

            if voice_leads_result.data:
                for call in voice_leads_result.data:
                    lead_info = call.get("lead_info") or {}
                    if lead_info.get("name") or lead_info.get("email") or lead_info.get("phone"):
                        voice_leads_count += 1
            total_leads += voice_leads_count

        # Get whatsapp leads from whatsapp_conversations.lead_info
        if agent_ids:
            wa_leads_result = supabase_admin.table("whatsapp_conversations").select(
                "id, agent_id, lead_info, created_at"
            ).in_("agent_id", agent_ids).not_.is_("lead_info", "null").execute()

            if wa_leads_result.data:
                for conv in wa_leads_result.data:
                    lead_info = conv.get("lead_info") or {}
                    if lead_info.get("name") or lead_info.get("email") or lead_info.get("phone"):
                        wa_leads_count += 1
            total_leads += wa_leads_count

        response["leads"]["recent"] = recent_leads_list
        response["leads"]["total_count"] = total_leads

        # Calculate conversion rates
        total_chatbot_interactions = response["chatbots"]["total_conversations"]
        total_voice_interactions = response["voice_assistants"]["total_calls"]
        total_wa_interactions = response["whatsapp_agents"]["total_conversations"]
        total_interactions = total_chatbot_interactions + total_voice_interactions + total_wa_interactions

        response["leads"]["conversion_rates"] = {
            "chatbot": round((chatbot_leads_count / total_chatbot_interactions) * 100) if total_chatbot_interactions > 0 else 0,
            "voice": round((voice_leads_count / total_voice_interactions) * 100) if total_voice_interactions > 0 else 0,
            "whatsapp": round((wa_leads_count / total_wa_interactions) * 100) if total_wa_interactions > 0 else 0,
            "overall": round((total_leads / total_interactions) * 100) if total_interactions > 0 else 0
        }

        # ========== SUPPORT TICKETS ==========
        tickets = supabase_admin.table("support_tickets").select(
            "id, subject, status, priority, created_at, updated_at"
        ).eq("customer_id", customer_id).order("updated_at", desc=True).limit(5).execute()

        response["support_tickets"]["recent"] = tickets.data or []
        response["support_tickets"]["open_count"] = len([
            t for t in (tickets.data or [])
            if t.get("status") in ["open", "in_progress"]
        ])

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get customer analytics error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/portal/sync-whatsapp-conversations")
async def sync_customer_whatsapp_conversations(auth_data: Dict = Depends(get_current_customer)):
    """
    Sync WhatsApp conversations from ElevenLabs for customer's assigned agents.
    This allows customers to see the latest conversations without admin intervention.
    """
    import httpx
    from datetime import datetime, timedelta

    try:
        user_id = auth_data["user_id"]

        # Get customer profile
        customer_result = supabase_admin.table("customers").select(
            "id"
        ).eq("user_id", user_id).single().execute()

        if not customer_result.data:
            raise HTTPException(status_code=404, detail="Customer profile not found")

        customer_id = customer_result.data["id"]

        # Get assigned WhatsApp agents
        assignments = supabase_admin.table("customer_whatsapp_agent_assignments").select(
            "agent_id"
        ).eq("customer_id", customer_id).execute()

        if not assignments.data:
            return {"success": True, "synced": 0, "message": "No WhatsApp agents assigned"}

        agent_ids = [a["agent_id"] for a in assignments.data]
        total_synced = 0

        for agent_id in agent_ids:
            try:
                # Get the agent's owner user_id
                agent_result = supabase_admin.table("whatsapp_agents").select(
                    "user_id"
                ).eq("id", agent_id).single().execute()

                if not agent_result.data:
                    continue

                owner_user_id = agent_result.data["user_id"]

                # Get owner's ElevenLabs connection
                conn_result = supabase_admin.table("elevenlabs_connections").select(
                    "api_key"
                ).eq("user_id", owner_user_id).eq("is_active", True).single().execute()

                if not conn_result.data:
                    continue

                api_key = conn_result.data["api_key"]

                async with httpx.AsyncClient(timeout=30.0) as client:
                    # Fetch conversations list from ElevenLabs
                    conv_list_response = await client.get(
                        "https://api.elevenlabs.io/v1/convai/conversations",
                        headers={"xi-api-key": api_key},
                        params={"agent_id": agent_id}
                    )

                    if conv_list_response.status_code != 200:
                        logger.warning(f"Failed to fetch WhatsApp conversations for agent {agent_id}")
                        continue

                    conv_list = conv_list_response.json()
                    conversations = conv_list.get("conversations", [])

                    for conv in conversations:
                        conversation_id = conv.get("conversation_id")
                        if not conversation_id:
                            continue

                        # Check if conversation already exists and has summary
                        existing = supabase_admin.table("whatsapp_conversations").select(
                            "id, summary"
                        ).eq("id", conversation_id).execute()

                        is_new = not existing.data
                        needs_summary = is_new or not existing.data[0].get("summary") if existing.data else True

                        # Fetch conversation details
                        conv_detail_response = await client.get(
                            f"https://api.elevenlabs.io/v1/convai/conversations/{conversation_id}",
                            headers={"xi-api-key": api_key}
                        )

                        if conv_detail_response.status_code != 200:
                            continue

                        conv_detail = conv_detail_response.json()
                        metadata = conv_detail.get("metadata", {})
                        transcript = conv_detail.get("transcript", [])
                        analysis = conv_detail.get("analysis", {})

                        # Get timestamps - ElevenLabs uses start_time_unix_secs (Unix timestamp)
                        start_unix = metadata.get("start_time_unix_secs") or conv.get("start_time_unix_secs")
                        call_duration = metadata.get("call_duration_secs") or conv.get("call_duration_secs") or 0

                        # Convert Unix timestamp to ISO format
                        started_at = None
                        ended_at = None
                        if start_unix:
                            started_at = datetime.utcfromtimestamp(start_unix).isoformat() + "Z"
                            if call_duration:
                                ended_at = datetime.utcfromtimestamp(start_unix + call_duration).isoformat() + "Z"
                        else:
                            # Fallback to current time if no timestamp available
                            started_at = datetime.utcnow().isoformat() + "Z"

                        # Get phone number from various possible locations per ElevenLabs API
                        phone_number = None

                        # Check conversation_initiation_client_data for dynamic variables
                        client_data = conv_detail.get("conversation_initiation_client_data", {})
                        dynamic_vars = client_data.get("dynamic_variables", {})
                        if dynamic_vars:
                            phone_number = dynamic_vars.get("system__caller_id") or dynamic_vars.get("system__called_number")

                        # Check metadata.phone_call for phone call metadata
                        if not phone_number and metadata.get("phone_call"):
                            phone_call = metadata["phone_call"]
                            phone_number = phone_call.get("from_number") or phone_call.get("to_number")

                        # Check metadata.whatsapp for WhatsApp metadata
                        if not phone_number and metadata.get("whatsapp"):
                            whatsapp = metadata["whatsapp"]
                            phone_number = whatsapp.get("phone_number") or whatsapp.get("from") or whatsapp.get("user_id")

                        # Upsert conversation
                        conv_data = {
                            "id": conversation_id,
                            "agent_id": agent_id,
                            "phone_number": phone_number,
                            "status": conv_detail.get("status", "done"),
                            "started_at": started_at,
                            "ended_at": ended_at
                        }

                        if analysis and analysis.get("transcript_summary"):
                            conv_data["summary"] = analysis.get("transcript_summary")

                        supabase_admin.table("whatsapp_conversations").upsert(
                            conv_data, on_conflict="id"
                        ).execute()

                        # Insert transcript messages if they don't exist
                        transcript_text = ""
                        if transcript:
                            existing_msgs = supabase_admin.table("whatsapp_messages").select(
                                "id, role, content"
                            ).eq("conversation_id", conversation_id).execute()

                            if not existing_msgs.data:
                                # Insert new messages
                                messages_to_insert = []
                                for msg in transcript:
                                    role = msg.get("role", "unknown")
                                    if role == "agent":
                                        role = "assistant"
                                    content = msg.get("message", "") or msg.get("text", "")
                                    if content:
                                        messages_to_insert.append({
                                            "id": str(uuid.uuid4()),
                                            "conversation_id": conversation_id,
                                            "role": role,
                                            "content": content,
                                            "timestamp": datetime.utcnow().isoformat()
                                        })

                                if messages_to_insert:
                                    supabase_admin.table("whatsapp_messages").insert(
                                        messages_to_insert
                                    ).execute()
                                    transcript_text = "\n".join([f"{m['role']}: {m['content']}" for m in messages_to_insert])
                            else:
                                # Build transcript from existing messages
                                transcript_text = "\n".join([f"{m['role']}: {m['content']}" for m in existing_msgs.data])

                        # Generate AI summary if needed (for new or existing conversations without summary)
                        if needs_summary and transcript_text.strip():
                            try:
                                from app.routers.whatsapp import generate_whatsapp_summary
                                summary = await generate_whatsapp_summary(conversation_id, transcript_text, owner_user_id)

                                if summary:
                                    supabase_admin.table("whatsapp_conversations").update({
                                        "summary": summary.get("summary"),
                                        "key_points": summary.get("key_points"),
                                        "action_items": summary.get("action_items"),
                                        "sentiment": summary.get("sentiment"),
                                        "sentiment_notes": summary.get("sentiment_notes"),
                                        "conversation_outcome": summary.get("conversation_outcome"),
                                        "topics_discussed": summary.get("topics_discussed"),
                                        "lead_info": summary.get("lead_info")
                                    }).eq("id", conversation_id).execute()

                                    # Extract lead to leads table if we have contact info
                                    lead_info = summary.get("lead_info", {})
                                    if lead_info and (lead_info.get("name") or lead_info.get("email") or lead_info.get("phone")):
                                        # Check if lead already exists for this conversation
                                        existing_lead = supabase_admin.table("leads").select(
                                            "id"
                                        ).eq("conversation_id", conversation_id).limit(1).execute()

                                        if not existing_lead.data:
                                            # Get agent name for source_name
                                            agent_info = supabase_admin.table("whatsapp_agents").select(
                                                "name"
                                            ).eq("id", agent_id).single().execute()
                                            agent_name = agent_info.data.get("name") if agent_info.data else None

                                            supabase_admin.table("leads").insert({
                                                "conversation_id": conversation_id,
                                                "source_id": agent_id,
                                                "source_type": "whatsapp",
                                                "source_name": agent_name,
                                                "user_id": owner_user_id,
                                                "name": lead_info.get("name"),
                                                "email": lead_info.get("email"),
                                                "phone_number": lead_info.get("phone") or phone_number,
                                                "additional_data": {
                                                    "company": lead_info.get("company"),
                                                    "interest_level": lead_info.get("interest_level"),
                                                    "notes": lead_info.get("notes")
                                                }
                                            }).execute()
                                            logger.info(f"Extracted lead from WhatsApp conversation {conversation_id}")
                            except Exception as ai_error:
                                logger.warning(f"Error generating AI summary for conversation {conversation_id}: {ai_error}")

                        total_synced += 1

            except Exception as e:
                logger.warning(f"Error syncing agent {agent_id}: {e}")
                continue

        return {
            "success": True,
            "synced": total_synced,
            "message": f"Synced {total_synced} new conversations"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Sync customer WhatsApp conversations error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/portal/sync-voice-calls")
async def sync_customer_voice_calls(auth_data: Dict = Depends(get_current_customer)):
    """
    Sync Voice Assistant calls from VAPI for customer's assigned assistants.
    This allows customers to see the latest calls without admin intervention.
    """
    import httpx
    from datetime import datetime

    try:
        user_id = auth_data["user_id"]

        # Get customer profile
        customer_result = supabase_admin.table("customers").select(
            "id"
        ).eq("user_id", user_id).single().execute()

        if not customer_result.data:
            raise HTTPException(status_code=404, detail="Customer profile not found")

        customer_id = customer_result.data["id"]

        # Get assigned voice assistants
        assignments = supabase_admin.table("customer_assistant_assignments").select(
            "assistant_id"
        ).eq("customer_id", customer_id).execute()

        if not assignments.data:
            return {"success": True, "synced": 0, "message": "No voice assistants assigned"}

        assistant_ids = [a["assistant_id"] for a in assignments.data]
        total_synced = 0

        for assistant_id in assistant_ids:
            try:
                # Get the assistant's owner user_id
                # Note: The database id IS the VAPI assistant ID (no separate vapi_assistant_id column)
                assistant_result = supabase_admin.table("voice_assistants").select(
                    "user_id"
                ).eq("id", assistant_id).single().execute()

                if not assistant_result.data:
                    continue

                owner_user_id = assistant_result.data["user_id"]
                # The assistant_id IS the VAPI assistant ID
                vapi_assistant_id = assistant_id

                # Get owner's VAPI connection (voice_connections table stores VAPI API keys)
                conn_result = supabase_admin.table("voice_connections").select(
                    "api_key"
                ).eq("user_id", owner_user_id).eq("is_active", True).execute()

                if not conn_result.data:
                    continue

                for conn in conn_result.data:
                    api_key = conn["api_key"]

                    async with httpx.AsyncClient(timeout=30.0) as client:
                        # Fetch calls from VAPI
                        params = {"limit": 50}
                        if vapi_assistant_id:
                            params["assistantId"] = vapi_assistant_id

                        calls_response = await client.get(
                            "https://api.vapi.ai/call",
                            headers={"Authorization": f"Bearer {api_key}"},
                            params=params
                        )

                        if calls_response.status_code != 200:
                            continue

                        calls = calls_response.json()
                        if isinstance(calls, dict):
                            calls = calls.get("calls", []) or calls.get("data", [])

                        for call in calls:
                            call_id = call.get("id")
                            if not call_id:
                                continue

                            # Check if call already exists
                            existing = supabase_admin.table("voice_assistant_calls").select(
                                "id"
                            ).eq("id", call_id).execute()

                            if existing.data:
                                continue  # Already synced

                            # Get call details
                            call_detail_response = await client.get(
                                f"https://api.vapi.ai/call/{call_id}",
                                headers={"Authorization": f"Bearer {api_key}"}
                            )

                            if call_detail_response.status_code != 200:
                                continue

                            call_detail = call_detail_response.json()
                            artifact = call_detail.get("artifact", {}) or {}

                            # Upsert call record (only columns that exist in voice_assistant_calls table)
                            call_data = {
                                "id": call_id,
                                "assistant_id": assistant_id,
                                "phone_number": call_detail.get("customer", {}).get("number"),
                                "status": call_detail.get("status", "completed"),
                                "started_at": call_detail.get("startedAt"),
                                "ended_at": call_detail.get("endedAt"),
                                "duration_seconds": call_detail.get("durationSeconds") or 0,
                            }

                            supabase_admin.table("voice_assistant_calls").upsert(
                                call_data, on_conflict="id"
                            ).execute()

                            # Insert transcript messages
                            messages = artifact.get("messages", []) or call_detail.get("messages", [])
                            if messages:
                                existing_msgs = supabase_admin.table("voice_assistant_transcripts").select(
                                    "id"
                                ).eq("call_id", call_id).limit(1).execute()

                                if not existing_msgs.data:
                                    transcripts_to_insert = []
                                    for msg in messages:
                                        role = msg.get("role", "unknown")
                                        if role in ["bot", "assistant"]:
                                            role = "assistant"
                                        elif role in ["user", "human", "customer"]:
                                            role = "user"
                                        content = msg.get("message") or msg.get("content") or msg.get("text", "")
                                        if content and role in ["assistant", "user"]:
                                            transcripts_to_insert.append({
                                                "call_id": call_id,
                                                "role": role,
                                                "content": content,
                                                "timestamp": datetime.utcnow().isoformat()
                                            })

                                    if transcripts_to_insert:
                                        supabase_admin.table("voice_assistant_transcripts").insert(
                                            transcripts_to_insert
                                        ).execute()

                                        # Generate AI summary for the call (safe - won't break sync if fails)
                                        try:
                                            transcript_text = "\n".join([
                                                f"{t['role']}: {t['content']}" for t in transcripts_to_insert
                                            ])
                                            if transcript_text.strip():
                                                from app.routers.webhooks import generate_call_summary
                                                summary = await generate_call_summary(call_id, transcript_text, owner_user_id)
                                                if summary:
                                                    supabase_admin.table("voice_assistant_calls").update({
                                                        "summary": summary.get("summary"),
                                                        "key_points": summary.get("key_points"),
                                                        "action_items": summary.get("action_items"),
                                                        "sentiment": summary.get("sentiment"),
                                                        "sentiment_notes": summary.get("sentiment_notes"),
                                                        "call_outcome": summary.get("call_outcome"),
                                                        "topics_discussed": summary.get("topics_discussed"),
                                                        "lead_info": summary.get("lead_info")
                                                    }).eq("id", call_id).execute()
                                                    logger.info(f"Generated AI summary for call {call_id}")
                                        except Exception as ai_error:
                                            logger.warning(f"AI summary generation failed for call {call_id}: {ai_error}")
                                            # Don't fail the sync - just skip AI summary

                            # Insert recording if available
                            recording_url = artifact.get("recordingUrl") or call_detail.get("recordingUrl")
                            if recording_url:
                                existing_rec = supabase_admin.table("voice_assistant_recordings").select(
                                    "id"
                                ).eq("call_id", call_id).limit(1).execute()

                                if not existing_rec.data:
                                    supabase_admin.table("voice_assistant_recordings").insert({
                                        "call_id": call_id,
                                        "recording_url": recording_url
                                    }).execute()

                            total_synced += 1

            except Exception as e:
                logger.warning(f"Error syncing assistant {assistant_id}: {e}")
                continue

        return {
            "success": True,
            "synced": total_synced,
            "message": f"Synced {total_synced} new calls"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Sync customer voice calls error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
