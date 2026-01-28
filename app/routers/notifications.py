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


def format_duration(seconds: int) -> str:
    """Format seconds into human readable duration"""
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        mins = seconds // 60
        secs = seconds % 60
        return f"{mins}m {secs}s"
    else:
        hours = seconds // 3600
        mins = (seconds % 3600) // 60
        return f"{hours}h {mins}m"


def generate_weekly_email_html(
    customer_name: str,
    week_range: str,
    portal_url: str,
    support_url: str,
    overall_stats: dict,
    chatbot_data: dict,
    voice_data: dict,
    whatsapp_data: dict,
    year: int,
    custom_logo_url: str = None,
    custom_primary_color: str = None,
    brand_name: str = "Voxtro"
) -> str:
    """Generate the dark-themed weekly email HTML with optional custom branding"""

    # Use custom branding if provided
    has_custom_branding = bool(custom_logo_url)
    logo_url = custom_logo_url or "https://ik.imagekit.io/wrewtbha2/Voxtro%20(1920%20x%201080%20px)%20(3).png"
    primary_color = custom_primary_color or "#e45133"
    display_brand_name = "" if has_custom_branding else "Voxtro"

    # Build chatbot section
    chatbot_section = ""
    if chatbot_data.get("enabled") and chatbot_data.get("items"):
        chatbot_rows = ""
        for item in chatbot_data["items"]:
            chatbot_rows += f'''
            <tr>
                <td style="padding:12px; font-size:13px; color:#ffffff; border-bottom:1px solid #1c1c20;">{item["name"]}</td>
                <td style="padding:12px; font-size:13px; color:#e8e8ea; border-bottom:1px solid #1c1c20;" align="right">{item["conversations"]}</td>
                <td style="padding:12px; font-size:13px; color:#e8e8ea; border-bottom:1px solid #1c1c20;" align="right">{item["messages"]}</td>
            </tr>'''

        chatbot_section = f'''
        <tr>
            <td style="padding:0 0 14px 0;">
              <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background:#151517; border:1px solid #242428; border-radius:16px;">
                <tr>
                  <td style="padding:18px;">
                    <div style="font-size:13px; color:#b6b6bb; line-height:18px; font-weight:800;">🤖 Chatbot Performance</div>
                    <div style="height:12px; line-height:12px;">&nbsp;</div>
                    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
                      <tr>
                        <td style="padding:0 8px 10px 0;" width="50%">
                          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#101012; border:1px solid #26262b; border-radius:14px;">
                            <tr><td style="padding:14px;">
                              <div style="font-size:12px; color:#b6b6bb; font-weight:800;">Conversations</div>
                              <div style="font-size:20px; color:#ffffff; font-weight:900; margin-top:4px;">{chatbot_data["total_conversations"]}</div>
                            </td></tr>
                          </table>
                        </td>
                        <td style="padding:0 0 10px 8px;" width="50%">
                          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#101012; border:1px solid #26262b; border-radius:14px;">
                            <tr><td style="padding:14px;">
                              <div style="font-size:12px; color:#b6b6bb; font-weight:800;">Messages</div>
                              <div style="font-size:20px; color:#ffffff; font-weight:900; margin-top:4px;">{chatbot_data["total_messages"]}</div>
                            </td></tr>
                          </table>
                        </td>
                      </tr>
                    </table>
                    <div style="height:6px; line-height:6px;">&nbsp;</div>
                    <div style="font-size:12px; color:#b6b6bb; font-weight:800; margin:8px 0 10px 0;">Per-chatbot breakdown</div>
                    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="border-collapse:separate; border-spacing:0; background:#101012; border:1px solid #26262b; border-radius:14px; overflow:hidden;">
                      <tr>
                        <td style="padding:12px; font-size:12px; font-weight:900; color:#b6b6bb; border-bottom:1px solid #26262b;">Chatbot</td>
                        <td style="padding:12px; font-size:12px; font-weight:900; color:#b6b6bb; border-bottom:1px solid #26262b;" align="right">Conversations</td>
                        <td style="padding:12px; font-size:12px; font-weight:900; color:#b6b6bb; border-bottom:1px solid #26262b;" align="right">Messages</td>
                      </tr>
                      {chatbot_rows}
                    </table>
                  </td>
                </tr>
              </table>
            </td>
        </tr>'''

    # Build voice section
    voice_section = ""
    if voice_data.get("enabled") and voice_data.get("items"):
        voice_rows = ""
        for item in voice_data["items"]:
            voice_rows += f'''
            <tr>
                <td style="padding:12px; font-size:13px; color:#ffffff; border-bottom:1px solid #1c1c20;">{item["name"]}</td>
                <td style="padding:12px; font-size:13px; color:#e8e8ea; border-bottom:1px solid #1c1c20;" align="right">{item["calls"]}</td>
                <td style="padding:12px; font-size:13px; color:#e8e8ea; border-bottom:1px solid #1c1c20;" align="right">{item["duration"]}</td>
            </tr>'''

        voice_section = f'''
        <tr>
            <td style="padding:0 0 14px 0;">
              <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background:#151517; border:1px solid #242428; border-radius:16px;">
                <tr>
                  <td style="padding:18px;">
                    <div style="font-size:13px; color:#b6b6bb; line-height:18px; font-weight:800;">📞 Voice Assistant Performance</div>
                    <div style="height:12px; line-height:12px;">&nbsp;</div>
                    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
                      <tr>
                        <td style="padding:0 8px 10px 0;" width="50%">
                          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#101012; border:1px solid #26262b; border-radius:14px;">
                            <tr><td style="padding:14px;">
                              <div style="font-size:12px; color:#b6b6bb; font-weight:800;">Total Calls</div>
                              <div style="font-size:20px; color:#ffffff; font-weight:900; margin-top:4px;">{voice_data["total_calls"]}</div>
                            </td></tr>
                          </table>
                        </td>
                        <td style="padding:0 0 10px 8px;" width="50%">
                          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#101012; border:1px solid #26262b; border-radius:14px;">
                            <tr><td style="padding:14px;">
                              <div style="font-size:12px; color:#b6b6bb; font-weight:800;">Total Duration</div>
                              <div style="font-size:20px; color:#ffffff; font-weight:900; margin-top:4px;">{voice_data["total_duration"]}</div>
                            </td></tr>
                          </table>
                        </td>
                      </tr>
                    </table>
                    <div style="height:6px; line-height:6px;">&nbsp;</div>
                    <div style="font-size:12px; color:#b6b6bb; font-weight:800; margin:8px 0 10px 0;">Per-assistant breakdown</div>
                    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="border-collapse:separate; border-spacing:0; background:#101012; border:1px solid #26262b; border-radius:14px; overflow:hidden;">
                      <tr>
                        <td style="padding:12px; font-size:12px; font-weight:900; color:#b6b6bb; border-bottom:1px solid #26262b;">Assistant</td>
                        <td style="padding:12px; font-size:12px; font-weight:900; color:#b6b6bb; border-bottom:1px solid #26262b;" align="right">Calls</td>
                        <td style="padding:12px; font-size:12px; font-weight:900; color:#b6b6bb; border-bottom:1px solid #26262b;" align="right">Duration</td>
                      </tr>
                      {voice_rows}
                    </table>
                  </td>
                </tr>
              </table>
            </td>
        </tr>'''

    # Build WhatsApp section
    whatsapp_section = ""
    if whatsapp_data.get("enabled") and whatsapp_data.get("items"):
        whatsapp_rows = ""
        for item in whatsapp_data["items"]:
            whatsapp_rows += f'''
            <tr>
                <td style="padding:12px; font-size:13px; color:#ffffff; border-bottom:1px solid #1c1c20;">{item["name"]}</td>
                <td style="padding:12px; font-size:13px; color:#e8e8ea; border-bottom:1px solid #1c1c20;" align="right">{item["conversations"]}</td>
                <td style="padding:12px; font-size:13px; color:#e8e8ea; border-bottom:1px solid #1c1c20;" align="right">{item["messages"]}</td>
            </tr>'''

        whatsapp_section = f'''
        <tr>
            <td style="padding:0 0 14px 0;">
              <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background:#151517; border:1px solid #242428; border-radius:16px;">
                <tr>
                  <td style="padding:18px;">
                    <div style="font-size:13px; color:#b6b6bb; line-height:18px; font-weight:800;">💬 WhatsApp Agent Performance</div>
                    <div style="height:12px; line-height:12px;">&nbsp;</div>
                    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
                      <tr>
                        <td style="padding:0 8px 10px 0;" width="50%">
                          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#101012; border:1px solid #26262b; border-radius:14px;">
                            <tr><td style="padding:14px;">
                              <div style="font-size:12px; color:#b6b6bb; font-weight:800;">Conversations</div>
                              <div style="font-size:20px; color:#ffffff; font-weight:900; margin-top:4px;">{whatsapp_data["total_conversations"]}</div>
                            </td></tr>
                          </table>
                        </td>
                        <td style="padding:0 0 10px 8px;" width="50%">
                          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#101012; border:1px solid #26262b; border-radius:14px;">
                            <tr><td style="padding:14px;">
                              <div style="font-size:12px; color:#b6b6bb; font-weight:800;">Messages</div>
                              <div style="font-size:20px; color:#ffffff; font-weight:900; margin-top:4px;">{whatsapp_data["total_messages"]}</div>
                            </td></tr>
                          </table>
                        </td>
                      </tr>
                    </table>
                    <div style="height:6px; line-height:6px;">&nbsp;</div>
                    <div style="font-size:12px; color:#b6b6bb; font-weight:800; margin:8px 0 10px 0;">Per-agent breakdown</div>
                    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="border-collapse:separate; border-spacing:0; background:#101012; border:1px solid #26262b; border-radius:14px; overflow:hidden;">
                      <tr>
                        <td style="padding:12px; font-size:12px; font-weight:900; color:#b6b6bb; border-bottom:1px solid #26262b;">Agent</td>
                        <td style="padding:12px; font-size:12px; font-weight:900; color:#b6b6bb; border-bottom:1px solid #26262b;" align="right">Conversations</td>
                        <td style="padding:12px; font-size:12px; font-weight:900; color:#b6b6bb; border-bottom:1px solid #26262b;" align="right">Messages</td>
                      </tr>
                      {whatsapp_rows}
                    </table>
                  </td>
                </tr>
              </table>
            </td>
        </tr>'''

    # Footer text - hide Voxtro if custom branding
    footer_text = f"© {year} {display_brand_name}. All rights reserved." if display_brand_name else f"© {year}. All rights reserved."
    preheader_text = f"Your weekly agent summary for {week_range} is ready."

    html = f'''<!DOCTYPE html>
<html lang="en" style="margin:0; padding:0;">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta name="color-scheme" content="dark" />
  <title>Weekly Agent Summary</title>
</head>
<body style="margin:0; padding:0; background-color:#0f0f10; font-family: Arial, Helvetica, sans-serif; color:#e8e8ea;">
  <div style="display:none; max-height:0; overflow:hidden; opacity:0; color:transparent;">
    {preheader_text}
  </div>
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color:#0f0f10; padding:28px 0;">
    <tr>
      <td align="center" style="padding:0 16px;">
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="640" style="width:640px; max-width:640px;">
          <!-- Header -->
          <tr>
            <td style="padding:0 0 14px 0;">
              <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background:#151517; border:1px solid #242428; border-radius:16px;">
                <tr>
                  <td style="padding:18px 18px 14px 18px;">
                    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
                      <tr>
                        <td align="left" style="vertical-align:middle;">
                          <img src="{logo_url}" width="110" alt="Logo" style="display:block; border:0; outline:none; text-decoration:none; height:auto; max-height:50px;" />
                        </td>
                        <td align="right" style="vertical-align:middle;">
                          <div style="font-size:12px; color:#b6b6bb; line-height:18px;">Weekly Summary</div>
                          <div style="font-size:13px; color:#ffffff; font-weight:800; line-height:18px;">{week_range}</div>
                        </td>
                      </tr>
                    </table>
                    <div style="height:12px; line-height:12px;">&nbsp;</div>
                    <div style="font-size:22px; line-height:30px; font-weight:900; color:#ffffff;">
                      Hi {customer_name}, here's your weekly performance.
                    </div>
                    <div style="font-size:14px; line-height:22px; color:#b6b6bb; margin-top:6px;">
                      Below is a breakdown of overall activity plus performance by channel for <span style="color:#ffffff; font-weight:700;">{week_range}</span>.
                    </div>
                    <div style="height:14px; line-height:14px;">&nbsp;</div>
                    <table role="presentation" cellpadding="0" cellspacing="0" border="0">
                      <tr>
                        <td align="left" style="border-radius:12px; background-color:{primary_color};">
                          <a href="{portal_url}" style="display:inline-block; padding:12px 16px; font-size:14px; font-weight:900; color:#0f0f10; text-decoration:none; border-radius:12px;" target="_blank">View in portal</a>
                        </td>
                        <td style="width:10px;">&nbsp;</td>
                        <td align="left" style="border-radius:12px; border:1px solid #2a2a2f; background-color:#151517;">
                          <a href="{support_url}" style="display:inline-block; padding:12px 16px; font-size:14px; font-weight:700; color:#e8e8ea; text-decoration:none; border-radius:12px;" target="_blank">Contact support</a>
                        </td>
                      </tr>
                    </table>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Overall Performance -->
          <tr>
            <td style="padding:0 0 14px 0;">
              <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background:#151517; border:1px solid #242428; border-radius:16px;">
                <tr>
                  <td style="padding:18px;">
                    <div style="font-size:13px; color:#b6b6bb; line-height:18px; font-weight:800;">📈 Overall Performance</div>
                    <div style="height:12px; line-height:12px;">&nbsp;</div>
                    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
                      <tr>
                        <td style="padding:0 8px 10px 0;" width="50%">
                          <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background:#101012; border:1px solid #26262b; border-radius:14px;">
                            <tr>
                              <td style="padding:14px;">
                                <div style="font-size:12px; color:#b6b6bb; font-weight:800;">Total Interactions</div>
                                <div style="font-size:22px; color:#ffffff; font-weight:900; margin-top:4px;">{overall_stats["total_interactions"]}</div>
                                <div style="font-size:12px; color:#b6b6bb; margin-top:2px;">(conversations + calls)</div>
                              </td>
                            </tr>
                          </table>
                        </td>
                        <td style="padding:0 0 10px 8px;" width="50%">
                          <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background:#101012; border:1px solid #26262b; border-radius:14px;">
                            <tr>
                              <td style="padding:14px;">
                                <div style="font-size:12px; color:#b6b6bb; font-weight:800;">Total Messages</div>
                                <div style="font-size:22px; color:#ffffff; font-weight:900; margin-top:4px;">{overall_stats["total_messages"]}</div>
                                <div style="font-size:12px; color:#b6b6bb; margin-top:2px;">(chatbot + WhatsApp)</div>
                              </td>
                            </tr>
                          </table>
                        </td>
                      </tr>
                      <tr>
                        <td style="padding:0 8px 0 0;" width="50%">
                          <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background:#101012; border:1px solid #26262b; border-radius:14px;">
                            <tr>
                              <td style="padding:14px;">
                                <div style="font-size:12px; color:#b6b6bb; font-weight:800;">Call Duration</div>
                                <div style="font-size:22px; color:#ffffff; font-weight:900; margin-top:4px;">{overall_stats["call_duration"]}</div>
                                <div style="font-size:12px; color:#b6b6bb; margin-top:2px;">(voice assistants)</div>
                              </td>
                            </tr>
                          </table>
                        </td>
                        <td style="padding:0 0 0 8px;" width="50%">
                          <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background:#101012; border:1px solid #26262b; border-radius:14px;">
                            <tr>
                              <td style="padding:14px;">
                                <div style="font-size:12px; color:#b6b6bb; font-weight:800;">Leads Generated</div>
                                <div style="font-size:22px; color:#ffffff; font-weight:900; margin-top:4px;">{overall_stats["leads_generated"]}</div>
                                <div style="font-size:12px; color:#b6b6bb; margin-top:2px;">(this week)</div>
                              </td>
                            </tr>
                          </table>
                        </td>
                      </tr>
                    </table>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          {chatbot_section}
          {voice_section}
          {whatsapp_section}

          <!-- Footer -->
          <tr>
            <td style="padding:0;">
              <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background:#0f0f10;">
                <tr>
                  <td style="padding:10px 8px 0 8px;">
                    <div style="font-size:12px; line-height:18px; color:#8e8e95; text-align:center;">
                      You're receiving this email because weekly summaries are enabled for your account.
                    </div>
                    <div style="height:14px; line-height:14px;">&nbsp;</div>
                    <div style="font-size:12px; line-height:18px; color:#8e8e95; text-align:center;">
                      {footer_text}
                    </div>
                    <div style="height:18px; line-height:18px;">&nbsp;</div>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>'''

    return html


@router.post("/weekly-update/{customer_id}")
async def send_weekly_update(customer_id: str, auth_data: Dict = Depends(get_current_user)):
    """Send weekly activity update email to a customer"""
    try:
        from app.database import supabase_admin
        from datetime import datetime, timedelta

        customer_result = supabase_admin.table("customers").select(
            "id, email, full_name, created_by_user_id, chatbot_id, voice_assistant_id, whatsapp_agent_id"
        ).eq("id", customer_id).single().execute()

        if not customer_result.data:
            raise HTTPException(status_code=404, detail="Customer not found")

        customer = customer_result.data
        now = datetime.utcnow()
        week_ago = (now - timedelta(days=7)).isoformat()
        week_range = f"{(now - timedelta(days=7)).strftime('%b %d')} - {now.strftime('%b %d, %Y')}"

        # Initialize stats
        total_interactions = 0
        total_messages = 0
        total_call_duration = 0

        chatbot_data = {"enabled": False, "items": [], "total_conversations": 0, "total_messages": 0}
        voice_data = {"enabled": False, "items": [], "total_calls": 0, "total_duration": "0m"}
        whatsapp_data = {"enabled": False, "items": [], "total_conversations": 0, "total_messages": 0}

        # Get chatbot data
        if customer.get("chatbot_id"):
            chatbot = supabase_admin.table("chatbots").select("id, name").eq("id", customer["chatbot_id"]).single().execute()
            convos = supabase_admin.table("conversations").select("id, messages").eq("chatbot_id", customer["chatbot_id"]).gte("created_at", week_ago).execute()

            convo_count = len(convos.data or [])
            msg_count = sum(len(c.get("messages", []) or []) for c in (convos.data or []))

            chatbot_data = {
                "enabled": True,
                "total_conversations": convo_count,
                "total_messages": msg_count,
                "items": [{"name": chatbot.data.get("name", "Chatbot") if chatbot.data else "Chatbot", "conversations": convo_count, "messages": msg_count}]
            }
            total_interactions += convo_count
            total_messages += msg_count

        # Get voice data
        if customer.get("voice_assistant_id"):
            assistant = supabase_admin.table("voice_assistants").select("id, name").eq("id", customer["voice_assistant_id"]).single().execute()
            calls = supabase_admin.table("voice_assistant_calls").select("id, duration").eq("assistant_id", customer["voice_assistant_id"]).gte("started_at", week_ago).execute()

            call_count = len(calls.data or [])
            duration_secs = sum(c.get("duration", 0) or 0 for c in (calls.data or []))

            voice_data = {
                "enabled": True,
                "total_calls": call_count,
                "total_duration": format_duration(duration_secs),
                "items": [{"name": assistant.data.get("name", "Voice Assistant") if assistant.data else "Voice Assistant", "calls": call_count, "duration": format_duration(duration_secs)}]
            }
            total_interactions += call_count
            total_call_duration += duration_secs

        # Get WhatsApp data
        if customer.get("whatsapp_agent_id"):
            agent = supabase_admin.table("whatsapp_agents").select("id, name").eq("id", customer["whatsapp_agent_id"]).single().execute()
            wa_convos = supabase_admin.table("whatsapp_conversations").select("id, transcript").eq("agent_id", customer["whatsapp_agent_id"]).gte("started_at", week_ago).execute()

            wa_convo_count = len(wa_convos.data or [])
            wa_msg_count = sum(len(c.get("transcript", []) or []) for c in (wa_convos.data or []))

            whatsapp_data = {
                "enabled": True,
                "total_conversations": wa_convo_count,
                "total_messages": wa_msg_count,
                "items": [{"name": agent.data.get("name", "WhatsApp Agent") if agent.data else "WhatsApp Agent", "conversations": wa_convo_count, "messages": wa_msg_count}]
            }
            total_interactions += wa_convo_count
            total_messages += wa_msg_count

        # Get leads
        leads_result = supabase_admin.table("leads").select("id", count="exact").eq("user_id", customer.get("created_by_user_id")).gte("created_at", week_ago).execute()

        # Get custom branding for the admin
        custom_logo_url = None
        custom_primary_color = None
        if customer.get("created_by_user_id"):
            branding_result = supabase_admin.table("branding_settings").select(
                "logo_url, primary_color"
            ).eq("user_id", customer["created_by_user_id"]).maybeSingle().execute()
            if branding_result.data:
                custom_logo_url = branding_result.data.get("logo_url")
                custom_primary_color = branding_result.data.get("primary_color")

        overall_stats = {
            "total_interactions": total_interactions,
            "total_messages": total_messages,
            "call_duration": format_duration(total_call_duration),
            "leads_generated": leads_result.count or 0
        }

        html_content = generate_weekly_email_html(
            customer_name=customer.get("full_name", "there"),
            week_range=week_range,
            portal_url=f"{settings.frontend_url}/customer-login",
            support_url=f"{settings.frontend_url}/customer-login",
            overall_stats=overall_stats,
            chatbot_data=chatbot_data,
            voice_data=voice_data,
            whatsapp_data=whatsapp_data,
            year=now.year,
            custom_logo_url=custom_logo_url,
            custom_primary_color=custom_primary_color
        )

        email = EmailNotification(
            to_email=customer["email"],
            subject=f"Your Weekly Agent Summary - {week_range}",
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

        now = datetime.utcnow()
        week_range = f"{(now - timedelta(days=7)).strftime('%b %d')} - {now.strftime('%b %d, %Y')}"

        for i, customer in enumerate(customers_result.data):
            try:
                # Initialize stats
                total_interactions = 0
                total_messages = 0
                total_call_duration = 0

                chatbot_data = {"enabled": False, "items": [], "total_conversations": 0, "total_messages": 0}
                voice_data = {"enabled": False, "items": [], "total_calls": 0, "total_duration": "0m"}
                whatsapp_data = {"enabled": False, "items": [], "total_conversations": 0, "total_messages": 0}

                # Get chatbot data
                if customer.get("chatbot_id"):
                    chatbot = supabase_admin.table("chatbots").select("id, name").eq("id", customer["chatbot_id"]).maybeSingle().execute()
                    convos = supabase_admin.table("conversations").select("id, messages").eq("chatbot_id", customer["chatbot_id"]).gte("created_at", week_ago).execute()

                    convo_count = len(convos.data or [])
                    msg_count = sum(len(c.get("messages", []) or []) for c in (convos.data or []))

                    chatbot_data = {
                        "enabled": True,
                        "total_conversations": convo_count,
                        "total_messages": msg_count,
                        "items": [{"name": chatbot.data.get("name", "Chatbot") if chatbot.data else "Chatbot", "conversations": convo_count, "messages": msg_count}]
                    }
                    total_interactions += convo_count
                    total_messages += msg_count

                # Get voice data
                if customer.get("voice_assistant_id"):
                    assistant = supabase_admin.table("voice_assistants").select("id, name").eq("id", customer["voice_assistant_id"]).maybeSingle().execute()
                    calls = supabase_admin.table("voice_assistant_calls").select("id, duration").eq("assistant_id", customer["voice_assistant_id"]).gte("started_at", week_ago).execute()

                    call_count = len(calls.data or [])
                    duration_secs = sum(c.get("duration", 0) or 0 for c in (calls.data or []))

                    voice_data = {
                        "enabled": True,
                        "total_calls": call_count,
                        "total_duration": format_duration(duration_secs),
                        "items": [{"name": assistant.data.get("name", "Voice Assistant") if assistant.data else "Voice Assistant", "calls": call_count, "duration": format_duration(duration_secs)}]
                    }
                    total_interactions += call_count
                    total_call_duration += duration_secs

                # Get WhatsApp data
                if customer.get("whatsapp_agent_id"):
                    agent = supabase_admin.table("whatsapp_agents").select("id, name").eq("id", customer["whatsapp_agent_id"]).maybeSingle().execute()
                    wa_convos = supabase_admin.table("whatsapp_conversations").select("id, transcript").eq("agent_id", customer["whatsapp_agent_id"]).gte("started_at", week_ago).execute()

                    wa_convo_count = len(wa_convos.data or [])
                    wa_msg_count = sum(len(c.get("transcript", []) or []) for c in (wa_convos.data or []))

                    whatsapp_data = {
                        "enabled": True,
                        "total_conversations": wa_convo_count,
                        "total_messages": wa_msg_count,
                        "items": [{"name": agent.data.get("name", "WhatsApp Agent") if agent.data else "WhatsApp Agent", "conversations": wa_convo_count, "messages": wa_msg_count}]
                    }
                    total_interactions += wa_convo_count
                    total_messages += wa_msg_count

                # Get leads
                leads_result = supabase_admin.table("leads").select("id", count="exact").eq("user_id", customer.get("created_by_user_id")).gte("created_at", week_ago).execute()

                # Get custom branding for the admin
                custom_logo_url = None
                custom_primary_color = None
                if customer.get("created_by_user_id"):
                    branding_result = supabase_admin.table("branding_settings").select(
                        "logo_url, primary_color"
                    ).eq("user_id", customer["created_by_user_id"]).maybeSingle().execute()
                    if branding_result.data:
                        custom_logo_url = branding_result.data.get("logo_url")
                        custom_primary_color = branding_result.data.get("primary_color")

                overall_stats = {
                    "total_interactions": total_interactions,
                    "total_messages": total_messages,
                    "call_duration": format_duration(total_call_duration),
                    "leads_generated": leads_result.count or 0
                }

                html_content = generate_weekly_email_html(
                    customer_name=customer.get("full_name", "there"),
                    week_range=week_range,
                    portal_url=f"{settings.frontend_url}/customer-login",
                    support_url=f"{settings.frontend_url}/customer-login",
                    overall_stats=overall_stats,
                    chatbot_data=chatbot_data,
                    voice_data=voice_data,
                    whatsapp_data=whatsapp_data,
                    year=now.year,
                    custom_logo_url=custom_logo_url,
                    custom_primary_color=custom_primary_color
                )

                email = EmailNotification(
                    to_email=customer["email"],
                    subject=f"Your Weekly Agent Summary - {week_range}",
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
