"""Notification and email endpoints"""
from fastapi import APIRouter, HTTPException, Depends
from typing import Dict
import logging
import httpx

from app.models.notification import EmailNotification, ContactFormRequest
from app.middleware.auth import get_current_user
from app.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter()
settings = get_settings()


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
                    "from": f"{email.from_name} <noreply@yourdomain.com>",
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
            to_email="support@yourdomain.com",  # Configure this
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
        <p><a href="https://yourdomain.com/customer-portal/tickets/{ticket_id}">View Ticket</a></p>
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
        <p><a href="https://yourdomain.com/admin/tickets/{ticket_id}">View Ticket</a></p>
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
