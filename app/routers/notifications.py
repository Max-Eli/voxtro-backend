"""Notification and email endpoints"""
from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Optional, List
import logging
import httpx
import asyncio

from app.models.notification import EmailNotification, ContactFormRequest, TeamInviteRequest
from app.middleware.auth import get_current_user
from app.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter()
settings = get_settings()

# Rate limiting settings for Resend API
EMAIL_RATE_LIMIT_DELAY = 0.5  # 500ms between emails (2 emails/second)
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2  # seconds


async def send_tool_automation_email(
    recipients: str,
    subject: str,
    body: str,
    chatbot_name: str,
    parameters: Dict
) -> bool:
    """
    Send email triggered by chatbot tool/action automation

    Args:
        recipients: Comma-separated email addresses (can include {{param}} placeholders)
        subject: Email subject (can include {{param}} placeholders)
        body: Email body template (can include {{param}} placeholders)
        chatbot_name: Name of the chatbot for {{bot_name}} replacement
        parameters: Dict of parameter values to substitute

    Returns:
        True if email sent successfully
    """
    try:
        # Replace placeholders in all fields
        def replace_placeholders(text: str) -> str:
            result = text.replace("{{bot_name}}", chatbot_name)
            for key, value in parameters.items():
                result = result.replace(f"{{{{{key}}}}}", str(value) if value else "")
            return result

        final_recipients = replace_placeholders(recipients)
        final_subject = replace_placeholders(subject)
        final_body = replace_placeholders(body)

        # Parse recipients (comma-separated)
        recipient_list = [r.strip() for r in final_recipients.split(",") if r.strip() and "@" in r]

        if not recipient_list:
            logger.warning("No valid recipients for tool automation email")
            return False

        # Format body as HTML
        html_body = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #7c3aed;">{final_subject}</h2>
            <div style="white-space: pre-wrap;">{final_body}</div>
            <hr style="margin-top: 30px; border: none; border-top: 1px solid #e5e7eb;">
            <p style="color: #6b7280; font-size: 12px;">
                Sent automatically by {chatbot_name} chatbot
            </p>
        </div>
        """

        # Use retry logic for tool automation emails
        for retry in range(MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        "https://api.resend.com/emails",
                        headers={
                            "Authorization": f"Bearer {settings.resend_api_key}",
                            "Content-Type": "application/json"
                        },
                        json={
                            "from": f"{chatbot_name} <dev@dev.voxtro.io>",
                            "to": recipient_list,
                            "subject": final_subject,
                            "html": html_body
                        }
                    )

                    if response.status_code == 200:
                        logger.info(f"Tool automation email sent to {recipient_list}")
                        return True
                    elif response.status_code == 429 and retry < MAX_RETRIES:
                        backoff_time = RETRY_BACKOFF_BASE * (2 ** retry)
                        logger.warning(f"Rate limited, retrying in {backoff_time}s (attempt {retry + 1}/{MAX_RETRIES})")
                        await asyncio.sleep(backoff_time)
                        continue
                    else:
                        logger.error(f"Tool automation email failed: {response.text}")
                        return False
            except Exception as e:
                if retry < MAX_RETRIES:
                    await asyncio.sleep(RETRY_BACKOFF_BASE * (2 ** retry))
                    continue
                logger.error(f"Tool automation email error: {e}")
                return False
        return False

    except Exception as e:
        logger.error(f"Tool automation email error: {e}")
        return False


async def send_email_with_retry(email: EmailNotification, retry_count: int = 0) -> Dict:
    """
    Send email via Resend with retry logic for rate limits.
    Returns dict with success status and optional error.
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {settings.resend_api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "from": f"{email.from_name} <dev@dev.voxtro.io>",
                    "to": [email.to_email],
                    "subject": email.subject,
                    "html": email.html_content
                }
            )

            if response.status_code == 200:
                return {"success": True, "id": response.json().get("id")}

            # Handle rate limiting (429 Too Many Requests)
            if response.status_code == 429 and retry_count < MAX_RETRIES:
                backoff_time = RETRY_BACKOFF_BASE * (2 ** retry_count)
                logger.warning(f"Rate limited by Resend, retrying in {backoff_time}s (attempt {retry_count + 1}/{MAX_RETRIES})")
                await asyncio.sleep(backoff_time)
                return await send_email_with_retry(email, retry_count + 1)

            error_msg = f"Failed to send email: {response.status_code} - {response.text}"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}

    except Exception as e:
        logger.error(f"Email error: {e}")
        return {"success": False, "error": str(e)}


@router.post("/email")
async def send_email(email: EmailNotification):
    """Send email via Resend"""
    result = await send_email_with_retry(email)
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Failed to send email"))
    return result


@router.post("/contact")
async def send_contact_form(form: ContactFormRequest):
    """Handle contact form submissions"""
    try:
        html_content = f"""
        <h2>New Contact Form Submission</h2>
        <p><strong>Name:</strong> {form.name}</p>
        <p><strong>Email:</strong> {form.email}</p>
        <p><strong>Subject:</strong> {form.subject}</p>
        <p><strong>Message:</strong></p>
        <p>{form.message}</p>
        """

        email = EmailNotification(
            to_email=settings.support_email,
            subject=f"Contact Form: {form.subject}",
            html_content=html_content,
            from_name="Voxtro Contact Form"
        )

        return await send_email(email)

    except Exception as e:
        logger.error(f"Contact form error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ticket-reply")
async def send_ticket_reply_notification(
    ticket_id: str,
    customer_email: str,
    reply_content: str
):
    """Send notification to customer when admin replies to ticket"""
    try:
        from app.database import supabase_admin

        # Get ticket details
        ticket = supabase_admin.table("support_tickets").select("*").eq(
            "id", ticket_id
        ).single().execute()

        if not ticket.data:
            raise HTTPException(status_code=404, detail="Ticket not found")

        html_content = f"""
        <h2>New Reply to Your Support Ticket</h2>
        <p><strong>Ticket:</strong> {ticket.data.get('subject')}</p>
        <p><strong>Reply:</strong></p>
        <p>{reply_content}</p>
        <p><a href="{settings.frontend_url}/customer-portal/tickets/{ticket_id}">View Ticket</a></p>
        """

        email = EmailNotification(
            to_email=customer_email,
            subject=f"Reply to your ticket: {ticket.data.get('subject')}",
            html_content=html_content,
            from_name="Voxtro Support"
        )

        return await send_email(email)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ticket reply notification error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/admin-ticket")
async def send_admin_ticket_notification(
    ticket_id: str,
    admin_email: str,
    customer_name: str,
    ticket_subject: str
):
    """Send notification to admin when new ticket is created"""
    try:
        html_content = f"""
        <h2>New Support Ticket Created</h2>
        <p><strong>From:</strong> {customer_name}</p>
        <p><strong>Subject:</strong> {ticket_subject}</p>
        <p><a href="{settings.frontend_url}/admin/tickets/{ticket_id}">View Ticket</a></p>
        """

        email = EmailNotification(
            to_email=admin_email,
            subject=f"New Ticket: {ticket_subject}",
            html_content=html_content,
            from_name="Voxtro Platform"
        )

        return await send_email(email)

    except Exception as e:
        logger.error(f"Admin ticket notification error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/weekly-update/{customer_id}")
async def send_weekly_update(customer_id: str, auth_data: Dict = Depends(get_current_user)):
    """Send weekly activity update email to a customer"""
    try:
        from app.database import supabase_admin
        from datetime import datetime, timedelta

        # Get customer info
        customer_result = supabase_admin.table("customers").select(
            "id, email, full_name, created_by_user_id, chatbot_id, voice_assistant_id, whatsapp_agent_id"
        ).eq("id", customer_id).single().execute()

        if not customer_result.data:
            raise HTTPException(status_code=404, detail="Customer not found")

        customer = customer_result.data
        week_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()

        # Get chatbot conversations
        chatbot_convos = 0
        if customer.get("chatbot_id"):
            convos_result = supabase_admin.table("conversations").select(
                "id", count="exact"
            ).eq("chatbot_id", customer["chatbot_id"]).gte("created_at", week_ago).execute()
            chatbot_convos = convos_result.count or 0

        # Get voice calls
        voice_calls = 0
        if customer.get("voice_assistant_id"):
            calls_result = supabase_admin.table("voice_assistant_calls").select(
                "id", count="exact"
            ).eq("assistant_id", customer["voice_assistant_id"]).gte("started_at", week_ago).execute()
            voice_calls = calls_result.count or 0

        # Get WhatsApp conversations
        whatsapp_convos = 0
        if customer.get("whatsapp_agent_id"):
            wa_result = supabase_admin.table("whatsapp_conversations").select(
                "id", count="exact"
            ).eq("agent_id", customer["whatsapp_agent_id"]).gte("started_at", week_ago).execute()
            whatsapp_convos = wa_result.count or 0

        # Get support tickets
        tickets_result = supabase_admin.table("support_tickets").select(
            "id, subject, status"
        ).eq("customer_id", customer_id).gte("created_at", week_ago).execute()

        open_tickets = [t for t in (tickets_result.data or []) if t["status"] == "open"]
        resolved_tickets = [t for t in (tickets_result.data or []) if t["status"] in ["resolved", "closed"]]

        # Get leads generated
        leads_result = supabase_admin.table("leads").select(
            "id", count="exact"
        ).eq("user_id", customer.get("created_by_user_id")).gte("created_at", week_ago).execute()

        total_interactions = chatbot_convos + voice_calls + whatsapp_convos
        leads_count = leads_result.count or 0

        # Build breakdown rows only for connected agents
        breakdown_rows = ""
        if customer.get("chatbot_id"):
            breakdown_rows += f"""
            <tr>
                <td style="padding: 12px 0; border-bottom: 1px solid #e2e8f0; color: #475569; font-size: 14px;">Chatbot Conversations</td>
                <td style="padding: 12px 0; border-bottom: 1px solid #e2e8f0; color: #0f172a; font-size: 14px; font-weight: 600; text-align: right;">{chatbot_convos}</td>
            </tr>"""
        if customer.get("voice_assistant_id"):
            breakdown_rows += f"""
            <tr>
                <td style="padding: 12px 0; border-bottom: 1px solid #e2e8f0; color: #475569; font-size: 14px;">Voice Assistant Calls</td>
                <td style="padding: 12px 0; border-bottom: 1px solid #e2e8f0; color: #0f172a; font-size: 14px; font-weight: 600; text-align: right;">{voice_calls}</td>
            </tr>"""
        if customer.get("whatsapp_agent_id"):
            breakdown_rows += f"""
            <tr>
                <td style="padding: 12px 0; border-bottom: 1px solid #e2e8f0; color: #475569; font-size: 14px;">WhatsApp Conversations</td>
                <td style="padding: 12px 0; border-bottom: 1px solid #e2e8f0; color: #0f172a; font-size: 14px; font-weight: 600; text-align: right;">{whatsapp_convos}</td>
            </tr>"""

        # Always show support tickets
        breakdown_rows += f"""
        <tr>
            <td style="padding: 12px 0; color: #475569; font-size: 14px;">Support Tickets</td>
            <td style="padding: 12px 0; color: #0f172a; font-size: 14px; font-weight: 600; text-align: right;">{len(tickets_result.data or [])} ({len(open_tickets)} open)</td>
        </tr>"""

        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
        </head>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; margin: 0; padding: 0; background-color: #f8fafc;">
            <div style="max-width: 600px; margin: 0 auto; padding: 40px 20px;">
                <div style="background-color: white; border-radius: 16px; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1), 0 2px 4px -1px rgba(0,0,0,0.06);">
                    <!-- Header -->
                    <div style="background: linear-gradient(135deg, #e45133 0%, #f97316 100%); padding: 32px; text-align: center;">
                        <img src="https://ik.imagekit.io/wrewtbha2/Voxtro%20(1920%20x%201080%20px)%20(3).png" alt="Voxtro" style="height: 40px; margin-bottom: 16px;" />
                        <h1 style="color: white; font-size: 24px; font-weight: 600; margin: 0;">Weekly Activity Report</h1>
                        <p style="color: rgba(255,255,255,0.9); font-size: 14px; margin: 8px 0 0 0;">{datetime.utcnow().strftime('%B %d, %Y')}</p>
                    </div>

                    <!-- Content -->
                    <div style="padding: 32px;">
                        <p style="color: #334155; font-size: 16px; line-height: 24px; margin: 0 0 24px 0;">
                            Hi {customer.get('full_name', 'there')},
                        </p>
                        <p style="color: #64748b; font-size: 15px; line-height: 24px; margin: 0 0 32px 0;">
                            Here's a summary of your AI agents' activity over the past week.
                        </p>

                        <!-- Stats Grid -->
                        <div style="display: table; width: 100%; margin-bottom: 32px;">
                            <div style="display: table-row;">
                                <div style="display: table-cell; width: 50%; padding: 0 8px 16px 0; vertical-align: top;">
                                    <div style="background: #f1f5f9; border-radius: 12px; padding: 20px; text-align: center;">
                                        <p style="color: #64748b; font-size: 13px; margin: 0 0 4px 0; text-transform: uppercase; letter-spacing: 0.5px;">Total Interactions</p>
                                        <p style="color: #0f172a; font-size: 32px; font-weight: 700; margin: 0;">{total_interactions}</p>
                                    </div>
                                </div>
                                <div style="display: table-cell; width: 50%; padding: 0 0 16px 8px; vertical-align: top;">
                                    <div style="background: #f1f5f9; border-radius: 12px; padding: 20px; text-align: center;">
                                        <p style="color: #64748b; font-size: 13px; margin: 0 0 4px 0; text-transform: uppercase; letter-spacing: 0.5px;">Leads Generated</p>
                                        <p style="color: #0f172a; font-size: 32px; font-weight: 700; margin: 0;">{leads_count}</p>
                                    </div>
                                </div>
                            </div>
                        </div>

                        <!-- Breakdown -->
                        <div style="background: #fafafa; border-radius: 12px; padding: 24px; margin-bottom: 32px;">
                            <h3 style="color: #0f172a; font-size: 14px; font-weight: 600; margin: 0 0 16px 0; text-transform: uppercase; letter-spacing: 0.5px;">Activity Breakdown</h3>
                            <table style="width: 100%; border-collapse: collapse;">
                                {breakdown_rows}
                            </table>
                        </div>

                        <!-- CTA Button -->
                        <div style="text-align: center; margin-bottom: 24px;">
                            <a href="{settings.frontend_url}/customer-login" style="display: inline-block; background: linear-gradient(135deg, #e45133 0%, #f97316 100%); color: white; font-size: 15px; font-weight: 600; text-decoration: none; padding: 14px 32px; border-radius: 8px;">
                                View Full Dashboard
                            </a>
                        </div>

                        <p style="color: #94a3b8; font-size: 13px; line-height: 20px; margin: 0; text-align: center;">
                            Questions? Reply to this email or open a support ticket in your portal.
                        </p>
                    </div>

                    <!-- Footer -->
                    <div style="background: #f8fafc; padding: 24px 32px; border-top: 1px solid #e2e8f0;">
                        <p style="color: #94a3b8; font-size: 12px; margin: 0; text-align: center;">
                            You're receiving this because you're a Voxtro customer.
                        </p>
                        <p style="color: #94a3b8; font-size: 12px; margin: 8px 0 0 0; text-align: center;">
                            © {datetime.utcnow().year} Voxtro. All rights reserved.
                        </p>
                    </div>
                </div>
            </div>
        </body>
        </html>
        """

        email = EmailNotification(
            to_email=customer["email"],
            subject=f"Your Weekly Activity Report - {datetime.utcnow().strftime('%B %d, %Y')}",
            html_content=html_content,
            from_name="Voxtro"
        )

        await send_email(email)

        return {"success": True, "customer_id": customer_id}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Weekly update email error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/weekly-update-batch")
async def send_weekly_updates_batch(auth_data: Dict = Depends(get_current_user)):
    """Send weekly update emails to all customers of the current user"""
    try:
        from app.database import supabase_admin

        user_id = auth_data["user_id"]

        # Get all customers created by this user
        customers_result = supabase_admin.table("customers").select(
            "id, email, full_name"
        ).eq("created_by_user_id", user_id).execute()

        if not customers_result.data:
            return {"success": True, "emails_sent": 0, "message": "No customers found"}

        emails_sent = 0
        errors = []

        for i, customer in enumerate(customers_result.data):
            try:
                await send_weekly_update(customer["id"], auth_data)
                emails_sent += 1
                # Add delay between emails to avoid rate limiting
                if i < len(customers_result.data) - 1:
                    await asyncio.sleep(EMAIL_RATE_LIMIT_DELAY)
            except Exception as e:
                errors.append({"customer_id": customer["id"], "error": str(e)})

        return {
            "success": True,
            "emails_sent": emails_sent,
            "total_customers": len(customers_result.data),
            "errors": errors if errors else None
        }

    except Exception as e:
        logger.error(f"Batch weekly update error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cron/weekly-emails")
async def cron_weekly_emails(cron_secret: str):
    """
    Cron job endpoint for sending weekly emails to ALL customers
    Secured with a secret key (no user auth needed)

    Set CRON_SECRET in your environment variables
    Call with: POST /api/notifications/cron/weekly-emails?cron_secret=YOUR_SECRET
    """
    import os
    from app.database import supabase_admin
    from datetime import datetime, timedelta

    expected_secret = os.environ.get("CRON_SECRET", "")

    if not expected_secret or cron_secret != expected_secret:
        raise HTTPException(status_code=401, detail="Invalid cron secret")

    try:
        # Get ALL customers with their connected agents
        customers_result = supabase_admin.table("customers").select(
            "id, email, full_name, created_by_user_id, chatbot_id, voice_assistant_id, whatsapp_agent_id"
        ).execute()

        if not customers_result.data:
            return {"success": True, "emails_sent": 0, "message": "No customers found"}

        week_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()
        emails_sent = 0
        errors = []

        for i, customer in enumerate(customers_result.data):
            try:
                # Get metrics based on connected agents
                chatbot_convos = 0
                voice_calls = 0
                whatsapp_convos = 0

                if customer.get("chatbot_id"):
                    convos_result = supabase_admin.table("conversations").select(
                        "id", count="exact"
                    ).eq("chatbot_id", customer["chatbot_id"]).gte("created_at", week_ago).execute()
                    chatbot_convos = convos_result.count or 0

                if customer.get("voice_assistant_id"):
                    calls_result = supabase_admin.table("voice_assistant_calls").select(
                        "id", count="exact"
                    ).eq("assistant_id", customer["voice_assistant_id"]).gte("started_at", week_ago).execute()
                    voice_calls = calls_result.count or 0

                if customer.get("whatsapp_agent_id"):
                    wa_result = supabase_admin.table("whatsapp_conversations").select(
                        "id", count="exact"
                    ).eq("agent_id", customer["whatsapp_agent_id"]).gte("started_at", week_ago).execute()
                    whatsapp_convos = wa_result.count or 0

                # Get support tickets
                tickets_result = supabase_admin.table("support_tickets").select(
                    "id, subject, status"
                ).eq("customer_id", customer["id"]).gte("created_at", week_ago).execute()

                open_tickets = [t for t in (tickets_result.data or []) if t["status"] == "open"]

                # Get leads generated for this customer's admin
                leads_result = supabase_admin.table("leads").select(
                    "id", count="exact"
                ).eq("user_id", customer.get("created_by_user_id")).gte("created_at", week_ago).execute()

                total_interactions = chatbot_convos + voice_calls + whatsapp_convos
                leads_count = leads_result.count or 0

                # Build breakdown rows only for connected agents
                breakdown_rows = ""
                if customer.get("chatbot_id"):
                    breakdown_rows += f"""
                    <tr>
                        <td style="padding: 12px 0; border-bottom: 1px solid #e2e8f0; color: #475569; font-size: 14px;">Chatbot Conversations</td>
                        <td style="padding: 12px 0; border-bottom: 1px solid #e2e8f0; color: #0f172a; font-size: 14px; font-weight: 600; text-align: right;">{chatbot_convos}</td>
                    </tr>"""
                if customer.get("voice_assistant_id"):
                    breakdown_rows += f"""
                    <tr>
                        <td style="padding: 12px 0; border-bottom: 1px solid #e2e8f0; color: #475569; font-size: 14px;">Voice Assistant Calls</td>
                        <td style="padding: 12px 0; border-bottom: 1px solid #e2e8f0; color: #0f172a; font-size: 14px; font-weight: 600; text-align: right;">{voice_calls}</td>
                    </tr>"""
                if customer.get("whatsapp_agent_id"):
                    breakdown_rows += f"""
                    <tr>
                        <td style="padding: 12px 0; border-bottom: 1px solid #e2e8f0; color: #475569; font-size: 14px;">WhatsApp Conversations</td>
                        <td style="padding: 12px 0; border-bottom: 1px solid #e2e8f0; color: #0f172a; font-size: 14px; font-weight: 600; text-align: right;">{whatsapp_convos}</td>
                    </tr>"""

                # Always show support tickets
                breakdown_rows += f"""
                <tr>
                    <td style="padding: 12px 0; color: #475569; font-size: 14px;">Support Tickets</td>
                    <td style="padding: 12px 0; color: #0f172a; font-size: 14px; font-weight: 600; text-align: right;">{len(tickets_result.data or [])} ({len(open_tickets)} open)</td>
                </tr>"""

                html_content = f"""
                <!DOCTYPE html>
                <html>
                <head>
                    <meta charset="utf-8">
                    <meta name="viewport" content="width=device-width, initial-scale=1.0">
                </head>
                <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; margin: 0; padding: 0; background-color: #f8fafc;">
                    <div style="max-width: 600px; margin: 0 auto; padding: 40px 20px;">
                        <div style="background-color: white; border-radius: 16px; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1), 0 2px 4px -1px rgba(0,0,0,0.06);">
                            <!-- Header -->
                            <div style="background: linear-gradient(135deg, #e45133 0%, #f97316 100%); padding: 32px; text-align: center;">
                                <img src="https://ik.imagekit.io/wrewtbha2/Voxtro%20(1920%20x%201080%20px)%20(3).png" alt="Voxtro" style="height: 40px; margin-bottom: 16px;" />
                                <h1 style="color: white; font-size: 24px; font-weight: 600; margin: 0;">Weekly Activity Report</h1>
                                <p style="color: rgba(255,255,255,0.9); font-size: 14px; margin: 8px 0 0 0;">{datetime.utcnow().strftime('%B %d, %Y')}</p>
                            </div>

                            <!-- Content -->
                            <div style="padding: 32px;">
                                <p style="color: #334155; font-size: 16px; line-height: 24px; margin: 0 0 24px 0;">
                                    Hi {customer.get('full_name', 'there')},
                                </p>
                                <p style="color: #64748b; font-size: 15px; line-height: 24px; margin: 0 0 32px 0;">
                                    Here's a summary of your AI agents' activity over the past week.
                                </p>

                                <!-- Stats Grid -->
                                <div style="display: table; width: 100%; margin-bottom: 32px;">
                                    <div style="display: table-row;">
                                        <div style="display: table-cell; width: 50%; padding: 0 8px 16px 0; vertical-align: top;">
                                            <div style="background: #f1f5f9; border-radius: 12px; padding: 20px; text-align: center;">
                                                <p style="color: #64748b; font-size: 13px; margin: 0 0 4px 0; text-transform: uppercase; letter-spacing: 0.5px;">Total Interactions</p>
                                                <p style="color: #0f172a; font-size: 32px; font-weight: 700; margin: 0;">{total_interactions}</p>
                                            </div>
                                        </div>
                                        <div style="display: table-cell; width: 50%; padding: 0 0 16px 8px; vertical-align: top;">
                                            <div style="background: #f1f5f9; border-radius: 12px; padding: 20px; text-align: center;">
                                                <p style="color: #64748b; font-size: 13px; margin: 0 0 4px 0; text-transform: uppercase; letter-spacing: 0.5px;">Leads Generated</p>
                                                <p style="color: #0f172a; font-size: 32px; font-weight: 700; margin: 0;">{leads_count}</p>
                                            </div>
                                        </div>
                                    </div>
                                </div>

                                <!-- Breakdown -->
                                <div style="background: #fafafa; border-radius: 12px; padding: 24px; margin-bottom: 32px;">
                                    <h3 style="color: #0f172a; font-size: 14px; font-weight: 600; margin: 0 0 16px 0; text-transform: uppercase; letter-spacing: 0.5px;">Activity Breakdown</h3>
                                    <table style="width: 100%; border-collapse: collapse;">
                                        {breakdown_rows}
                                    </table>
                                </div>

                                <!-- CTA Button -->
                                <div style="text-align: center; margin-bottom: 24px;">
                                    <a href="{settings.frontend_url}/customer-login" style="display: inline-block; background: linear-gradient(135deg, #e45133 0%, #f97316 100%); color: white; font-size: 15px; font-weight: 600; text-decoration: none; padding: 14px 32px; border-radius: 8px;">
                                        View Full Dashboard
                                    </a>
                                </div>

                                <p style="color: #94a3b8; font-size: 13px; line-height: 20px; margin: 0; text-align: center;">
                                    Questions? Reply to this email or open a support ticket in your portal.
                                </p>
                            </div>

                            <!-- Footer -->
                            <div style="background: #f8fafc; padding: 24px 32px; border-top: 1px solid #e2e8f0;">
                                <p style="color: #94a3b8; font-size: 12px; margin: 0; text-align: center;">
                                    You're receiving this because you're a Voxtro customer.
                                </p>
                                <p style="color: #94a3b8; font-size: 12px; margin: 8px 0 0 0; text-align: center;">
                                    © {datetime.utcnow().year} Voxtro. All rights reserved.
                                </p>
                            </div>
                        </div>
                    </div>
                </body>
                </html>
                """

                email = EmailNotification(
                    to_email=customer["email"],
                    subject=f"Your Weekly Activity Report - {datetime.utcnow().strftime('%B %d, %Y')}",
                    html_content=html_content,
                    from_name="Voxtro"
                )

                result = await send_email_with_retry(email)
                if result.get("success"):
                    emails_sent += 1
                else:
                    errors.append({"customer_id": customer["id"], "error": result.get("error")})

                # Add delay between emails to avoid rate limiting
                if i < len(customers_result.data) - 1:
                    await asyncio.sleep(EMAIL_RATE_LIMIT_DELAY)

            except Exception as e:
                errors.append({"customer_id": customer["id"], "error": str(e)})
                logger.error(f"Weekly email error for customer {customer['id']}: {e}")

        logger.info(f"Cron weekly emails: sent {emails_sent} emails")

        return {
            "success": True,
            "emails_sent": emails_sent,
            "total_customers": len(customers_result.data),
            "errors": errors[:10] if errors else None  # Limit error details
        }

    except Exception as e:
        logger.error(f"Cron weekly emails error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/team-invite")
async def send_team_invite(invite: TeamInviteRequest, auth_data: Dict = Depends(get_current_user)):
    """Send team invitation email"""
    try:
        inviter_text = f"<strong>{invite.inviter_name}</strong> has" if invite.inviter_name else "You have been"

        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
        </head>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; margin: 0; padding: 0; background-color: #f4f4f5;">
            <div style="max-width: 600px; margin: 0 auto; padding: 40px 20px;">
                <div style="background-color: white; border-radius: 12px; padding: 40px; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">
                    <!-- Logo -->
                    <div style="text-align: center; margin-bottom: 32px;">
                        <img src="https://ik.imagekit.io/wrewtbha2/Voxtro%20(1920%20x%201080%20px)%20(3).png" alt="Voxtro" style="height: 48px;" />
                    </div>

                    <!-- Content -->
                    <h1 style="color: #18181b; font-size: 24px; font-weight: 600; margin: 0 0 16px 0; text-align: center;">
                        You're invited to join a team!
                    </h1>

                    <p style="color: #52525b; font-size: 16px; line-height: 24px; margin: 0 0 24px 0; text-align: center;">
                        {inviter_text} invited you to join <strong>{invite.team_name}</strong> on Voxtro.
                    </p>

                    <p style="color: #71717a; font-size: 14px; line-height: 22px; margin: 0 0 32px 0; text-align: center;">
                        Join this team to collaborate on tasks, support tickets, voice assistants, and more.
                    </p>

                    <!-- CTA Button -->
                    <div style="text-align: center; margin-bottom: 32px;">
                        <a href="{invite.invite_url}" style="display: inline-block; background-color: #e45133; color: white; font-size: 16px; font-weight: 600; text-decoration: none; padding: 14px 32px; border-radius: 8px;">
                            Accept Invitation
                        </a>
                    </div>

                    <p style="color: #a1a1aa; font-size: 12px; line-height: 20px; margin: 0; text-align: center;">
                        This invitation will expire in 7 days. If you didn't expect this invitation, you can safely ignore this email.
                    </p>

                    <!-- Link fallback -->
                    <div style="margin-top: 24px; padding-top: 24px; border-top: 1px solid #e4e4e7;">
                        <p style="color: #71717a; font-size: 12px; line-height: 18px; margin: 0; text-align: center;">
                            If the button doesn't work, copy and paste this link into your browser:
                        </p>
                        <p style="color: #3b82f6; font-size: 12px; line-height: 18px; margin: 8px 0 0 0; text-align: center; word-break: break-all;">
                            {invite.invite_url}
                        </p>
                    </div>
                </div>

                <!-- Footer -->
                <div style="text-align: center; margin-top: 24px;">
                    <p style="color: #a1a1aa; font-size: 12px; margin: 0;">
                        © 2025 Voxtro. All rights reserved.
                    </p>
                </div>
            </div>
        </body>
        </html>
        """

        email = EmailNotification(
            to_email=invite.email,
            subject=f"You've been invited to join {invite.team_name} on Voxtro",
            html_content=html_content,
            from_name="Voxtro"
        )

        result = await send_email_with_retry(email)

        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("error", "Failed to send invitation email"))

        logger.info(f"Team invitation email sent to {invite.email} for team {invite.team_name}")
        return {"success": True, "email_id": result.get("id"), "recipient": invite.email}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Team invite email error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
