"""Notification and email endpoints"""
from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Optional, List
import logging
import httpx

from app.models.notification import EmailNotification, ContactFormRequest
from app.middleware.auth import get_current_user
from app.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter()
settings = get_settings()


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
            else:
                logger.error(f"Tool automation email failed: {response.text}")
                return False

    except Exception as e:
        logger.error(f"Tool automation email error: {e}")
        return False


@router.post("/email")
async def send_email(email: EmailNotification):
    """Send email via Resend"""
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

            if response.status_code != 200:
                raise HTTPException(status_code=500, detail="Failed to send email")

            return {"success": True, "id": response.json().get("id")}

    except Exception as e:
        logger.error(f"Email error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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
            "id, email, full_name, created_by_user_id, chatbot_id"
        ).eq("id", customer_id).single().execute()

        if not customer_result.data:
            raise HTTPException(status_code=404, detail="Customer not found")

        customer = customer_result.data

        # Get activity from the past week
        week_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()

        # Get conversations count
        conversations_result = supabase_admin.table("conversations").select(
            "id", count="exact"
        ).eq("visitor_id", customer.get("user_id", "")).gte("created_at", week_ago).execute()

        # Get support tickets
        tickets_result = supabase_admin.table("support_tickets").select(
            "id, subject, status"
        ).eq("customer_id", customer_id).gte("created_at", week_ago).execute()

        open_tickets = [t for t in (tickets_result.data or []) if t["status"] == "open"]
        resolved_tickets = [t for t in (tickets_result.data or []) if t["status"] in ["resolved", "closed"]]

        html_content = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #7c3aed;">Weekly Activity Update</h2>
            <p>Hi {customer.get('full_name', 'Valued Customer')},</p>
            <p>Here's your weekly summary:</p>

            <div style="background: #f3f4f6; padding: 20px; border-radius: 8px; margin: 20px 0;">
                <h3 style="margin-top: 0;">📊 This Week's Activity</h3>
                <ul style="list-style: none; padding: 0;">
                    <li>💬 <strong>{conversations_result.count or 0}</strong> conversations</li>
                    <li>🎫 <strong>{len(tickets_result.data or [])}</strong> support tickets created</li>
                    <li>✅ <strong>{len(resolved_tickets)}</strong> tickets resolved</li>
                    <li>⏳ <strong>{len(open_tickets)}</strong> tickets pending</li>
                </ul>
            </div>

            <p>
                <a href="{settings.frontend_url}/customer-portal"
                   style="background: #7c3aed; color: white; padding: 12px 24px;
                          text-decoration: none; border-radius: 6px; display: inline-block;">
                    View Your Portal
                </a>
            </p>

            <p style="color: #6b7280; font-size: 14px; margin-top: 30px;">
                Questions? Reply to this email or open a support ticket.
            </p>
        </div>
        """

        email = EmailNotification(
            to_email=customer["email"],
            subject=f"Your Weekly Update - {datetime.utcnow().strftime('%B %d, %Y')}",
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

        for customer in customers_result.data:
            try:
                await send_weekly_update(customer["id"], auth_data)
                emails_sent += 1
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
