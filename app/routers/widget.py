"""Widget endpoints - For embeddable chatbots"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
import logging

from app.models.widget import (
    WidgetConfig,
    WidgetMessageRequest,
    WidgetMessageResponse
)
from app.database import supabase_admin
from app.routers.chat import handle_chat_message
from app.models.chat import ChatMessageRequest

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/{chatbot_id}/config", response_model=WidgetConfig)
async def get_widget_config(chatbot_id: str):
    """
    Get widget configuration (PUBLIC endpoint - no auth required)
    Used by embedded widgets on customer websites
    """
    try:
        # Get chatbot with forms and FAQs
        chatbot_result = supabase_admin.table("chatbots").select(
            "*"
        ).eq("id", chatbot_id).eq("is_active", True).single().execute()

        if not chatbot_result.data:
            raise HTTPException(status_code=404, detail="Chatbot not found")

        chatbot = chatbot_result.data

        # Get forms
        forms_result = supabase_admin.table("chatbot_forms").select(
            "*"
        ).eq("chatbot_id", chatbot_id).execute()

        # Get FAQs
        faqs_result = supabase_admin.table("chatbot_faqs").select(
            "*"
        ).eq("chatbot_id", chatbot_id).eq("is_active", True).order(
            "sort_order"
        ).execute()

        return WidgetConfig(
            chatbot_id=chatbot["id"],
            name=chatbot.get("name", "Assistant"),
            theme={
                "primary_color": chatbot.get("primary_color"),
                "secondary_color": chatbot.get("secondary_color"),
                "position": chatbot.get("widget_position", "bottom-right"),
                "avatar": chatbot.get("avatar_url")
            },
            first_message=chatbot.get("first_message"),
            placeholder_text=chatbot.get("placeholder_text", "Type your message..."),
            forms=forms_result.data or [],
            faqs=faqs_result.data or [],
            widget_position=chatbot.get("widget_position", "bottom-right"),
            primary_color=chatbot.get("primary_color"),
            secondary_color=chatbot.get("secondary_color")
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Widget config error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{chatbot_id}/message", response_model=WidgetMessageResponse)
async def widget_message(chatbot_id: str, request: WidgetMessageRequest):
    """
    Handle message from widget (PUBLIC endpoint - no auth required)
    This is called by embedded widgets on customer websites
    """
    try:
        # Create chat request
        chat_request = ChatMessageRequest(
            chatbot_id=chatbot_id,
            conversation_id=request.conversation_id,
            visitor_id=request.visitor_id,
            message=request.message,
            preview_mode=False
        )

        # Use the main chat handler (without auth)
        response = await handle_chat_message(chat_request, auth_data=None)

        return WidgetMessageResponse(
            conversation_id=response.conversation_id,
            message=response.message,
            actions=response.actions
        )

    except Exception as e:
        logger.error(f"Widget message error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{chatbot_id}/script.js")
async def get_widget_script(chatbot_id: str):
    """
    Serve widget JavaScript file (PUBLIC endpoint)
    This is the embed script that customers add to their websites
    """
    script = f"""
(function() {{
    'use strict';

    const CHATBOT_ID = '{chatbot_id}';
    const API_URL = window.VOXTRO_API_URL || 'https://voxtro-backend.onrender.com';

    // Generate unique visitor ID
    function getVisitorId() {{
        let visitorId = localStorage.getItem('voxtro_visitor_id');
        if (!visitorId) {{
            visitorId = 'visitor_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
            localStorage.setItem('voxtro_visitor_id', visitorId);
        }}
        return visitorId;
    }}

    // Fetch widget configuration
    async function loadConfig() {{
        try {{
            const response = await fetch(`${{API_URL}}/api/widget/${{CHATBOT_ID}}/config`);
            if (!response.ok) throw new Error('Failed to load widget');
            return await response.json();
        }} catch (error) {{
            console.error('Voxtro widget error:', error);
            return null;
        }}
    }}

    // Send message to chatbot
    async function sendMessage(message, conversationId) {{
        const response = await fetch(`${{API_URL}}/api/widget/${{CHATBOT_ID}}/message`, {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{
                chatbot_id: CHATBOT_ID,
                visitor_id: getVisitorId(),
                message: message,
                conversation_id: conversationId
            }})
        }});

        if (!response.ok) throw new Error('Failed to send message');
        return await response.json();
    }}

    // Create widget UI
    async function initWidget() {{
        const config = await loadConfig();
        if (!config) return;

        // Create widget container
        const container = document.createElement('div');
        container.id = 'voxtro-widget';
        container.style.cssText = `
            position: fixed;
            ${{config.widget_position.includes('right') ? 'right: 20px' : 'left: 20px'}};
            bottom: 20px;
            width: 400px;
            max-width: calc(100vw - 40px);
            height: 600px;
            max-height: calc(100vh - 100px);
            z-index: 999999;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        `;

        // Create iframe for widget content
        const iframe = document.createElement('iframe');
        iframe.style.cssText = `
            width: 100%;
            height: 100%;
            border: none;
            border-radius: 16px;
            box-shadow: 0 8px 32px rgba(0,0,0,0.12);
        `;

        // Set iframe src to your messenger page
        iframe.src = `${{window.location.origin}}/messenger/${{CHATBOT_ID}}`;

        container.appendChild(iframe);
        document.body.appendChild(container);
    }}

    // Initialize when DOM is ready
    if (document.readyState === 'loading') {{
        document.addEventListener('DOMContentLoaded', initWidget);
    }} else {{
        initWidget();
    }}
}})();
"""

    return Response(content=script, media_type="application/javascript")
