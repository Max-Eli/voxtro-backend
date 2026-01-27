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

        # Extract theme colors - support both solid and gradient
        theme_color_type = chatbot.get("theme_color_type", "solid")
        
        if theme_color_type == "gradient":
            # For gradients, return gradient info
            primary_color = chatbot.get("theme_gradient_start") or "#6366f1"
            gradient_end = chatbot.get("theme_gradient_end") or "#8b5cf6"
            gradient_angle = chatbot.get("theme_gradient_angle") or 135
        else:
            # Solid color - use theme_color
            primary_color = chatbot.get("theme_color") or "#6366f1"
            gradient_end = None
            gradient_angle = None

        # Widget appearance
        widget_button_color = chatbot.get("widget_button_color") or primary_color
        widget_text_color = chatbot.get("widget_text_color") or "#ffffff"
        widget_position = chatbot.get("widget_position") or "bottom-right"
        widget_size = chatbot.get("widget_size") or "medium"
        widget_border_radius = chatbot.get("widget_border_radius") or "rounded"
        widget_button_text = chatbot.get("widget_button_text") or "Chat with us"
        
        # Overlay settings
        widget_overlay_color = chatbot.get("widget_overlay_color")
        widget_overlay_opacity = chatbot.get("widget_overlay_opacity")
        widget_fullscreen = chatbot.get("widget_fullscreen")
        widget_custom_css = chatbot.get("widget_custom_css")
        hide_branding = chatbot.get("hide_branding", False)

        # Messages
        welcome_message = chatbot.get("welcome_message") or chatbot.get("first_message")
        avatar_url = chatbot.get("avatar_url")

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
                "color_type": theme_color_type,
                "primary_color": primary_color,
                "gradient_end": gradient_end,
                "gradient_angle": gradient_angle,
                "position": widget_position,
                "avatar": avatar_url
            },
            first_message=welcome_message,
            welcome_message=welcome_message,  # Send both for frontend compatibility
            placeholder_text=chatbot.get("placeholder_text") or "Type your message...",
            forms=forms_result.data or [],
            faqs=faqs_result.data or [],
            # Widget styling
            widget_position=widget_position,
            primary_color=primary_color,
            secondary_color=widget_text_color,
            widget_button_color=widget_button_color,
            widget_text_color=widget_text_color,
            widget_size=widget_size,
            widget_border_radius=widget_border_radius,
            widget_button_text=widget_button_text,
            widget_overlay_color=widget_overlay_color,
            widget_overlay_opacity=widget_overlay_opacity,
            widget_fullscreen=widget_fullscreen,
            widget_custom_css=widget_custom_css,
            hide_branding=hide_branding,
            # Gradient support
            theme_color_type=theme_color_type,
            theme_gradient_start=primary_color if theme_color_type == "gradient" else None,
            theme_gradient_end=gradient_end,
            theme_gradient_angle=gradient_angle
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
    Must work on ANY website (multi-tenant) - CORS headers are critical
    """
    from app.config import get_settings
    settings = get_settings()
    frontend_url = settings.frontend_url

    script = f"""
(function() {{
    'use strict';

    const CHATBOT_ID = '{chatbot_id}';
    const API_URL = window.VOXTRO_API_URL || 'https://voxtro-backend.onrender.com';
    const FRONTEND_URL = window.VOXTRO_FRONTEND_URL || '{frontend_url}';

    let isOpen = false;
    let config = null;
    let chatContainer = null;
    let toggleButton = null;

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

    // Toggle chat open/close
    function toggleChat() {{
        isOpen = !isOpen;
        if (chatContainer) {{
            chatContainer.style.display = isOpen ? 'block' : 'none';
        }}
        if (toggleButton) {{
            toggleButton.innerHTML = isOpen ? getCloseIcon() : getChatIcon();
        }}
    }}

    // Chat bubble icon SVG
    function getChatIcon() {{
        return `<svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>`;
    }}

    // Close icon SVG
    function getCloseIcon() {{
        return `<svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>`;
    }}

    // Create widget UI
    async function initWidget() {{
        config = await loadConfig();
        if (!config) return;

        // Get button color - prefer widget_button_color, fall back to primary_color or theme_color
        const buttonColor = config.widget_button_color || config.primary_color || '#6366f1';
        const textColor = config.widget_text_color || '#ffffff';
        const position = config.widget_position || 'bottom-right';
        const isRight = position.includes('right');
        const buttonText = config.widget_button_text;
        const size = config.widget_size || 'medium';
        const borderRadius = config.widget_border_radius || 'rounded';
        
        // Size mappings
        const sizeMap = {{ small: 50, medium: 60, large: 70 }};
        const buttonSize = sizeMap[size] || 60;
        
        // Border radius mappings
        const radiusMap = {{ square: '8px', rounded: '50%', pill: '30px' }};
        const radius = radiusMap[borderRadius] || '50%';

        // Support gradient backgrounds
        let buttonBackground = buttonColor;
        if (config.theme_color_type === 'gradient' && config.theme_gradient_start && config.theme_gradient_end) {{
            const angle = config.theme_gradient_angle || 135;
            buttonBackground = `linear-gradient(${{angle}}deg, ${{config.theme_gradient_start}}, ${{config.theme_gradient_end}})`;
        }}

        // Create main wrapper
        const wrapper = document.createElement('div');
        wrapper.id = 'voxtro-widget-wrapper';
        wrapper.style.cssText = `
            position: fixed;
            ${{isRight ? 'right: 20px' : 'left: 20px'}};
            bottom: 20px;
            z-index: 999999;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        `;

        // Create chat container (hidden by default)
        chatContainer = document.createElement('div');
        chatContainer.id = 'voxtro-chat-container';
        chatContainer.style.cssText = `
            display: none;
            width: 400px;
            max-width: calc(100vw - 40px);
            height: 600px;
            max-height: calc(100vh - 140px);
            margin-bottom: 16px;
            border-radius: 16px;
            overflow: hidden;
            box-shadow: 0 8px 32px rgba(0,0,0,0.15);
        `;

        // Create iframe for widget content
        const iframe = document.createElement('iframe');
        iframe.id = 'voxtro-chat-iframe';
        iframe.style.cssText = `
            width: 100%;
            height: 100%;
            border: none;
        `;
        iframe.src = `${{FRONTEND_URL}}/messenger/${{CHATBOT_ID}}`;

        chatContainer.appendChild(iframe);

        // Create toggle button
        toggleButton = document.createElement('button');
        toggleButton.id = 'voxtro-toggle-button';
        toggleButton.innerHTML = buttonText ? `<span style="margin-right: 8px;">${{getChatIcon()}}</span>${{buttonText}}` : getChatIcon();
        
        // Adjust button style based on whether text is present
        const buttonWidth = buttonText ? 'auto' : `${{buttonSize}}px`;
        const buttonPadding = buttonText ? '12px 20px' : '0';
        const actualRadius = buttonText ? '30px' : radius;
        
        toggleButton.style.cssText = `
            min-width: ${{buttonSize}}px;
            width: ${{buttonWidth}};
            height: ${{buttonSize}}px;
            padding: ${{buttonPadding}};
            border-radius: ${{actualRadius}};
            border: none;
            background: ${{buttonBackground}};
            color: ${{textColor}};
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            box-shadow: 0 4px 16px rgba(0,0,0,0.2);
            transition: transform 0.2s ease, box-shadow 0.2s ease;
            ${{isRight ? 'margin-left: auto;' : ''}}
        `;
        toggleButton.onmouseenter = function() {{
            this.style.transform = 'scale(1.1)';
            this.style.boxShadow = '0 6px 20px rgba(0,0,0,0.25)';
        }};
        toggleButton.onmouseleave = function() {{
            this.style.transform = 'scale(1)';
            this.style.boxShadow = '0 4px 16px rgba(0,0,0,0.2)';
        }};
        toggleButton.onclick = toggleChat;

        // Button container for alignment
        const buttonContainer = document.createElement('div');
        buttonContainer.style.cssText = `display: flex; justify-content: ${{isRight ? 'flex-end' : 'flex-start'}};`;
        buttonContainer.appendChild(toggleButton);

        wrapper.appendChild(chatContainer);
        wrapper.appendChild(buttonContainer);
        document.body.appendChild(wrapper);
    }}

    // Initialize when DOM is ready
    if (document.readyState === 'loading') {{
        document.addEventListener('DOMContentLoaded', initWidget);
    }} else {{
        initWidget();
    }}
}})();
"""

    # Return script with explicit CORS headers for multi-tenant support
    # This script must be loadable from ANY website
    return Response(
        content=script,
        media_type="application/javascript",
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "*",
            "Cache-Control": "no-cache, no-store, must-revalidate",
        }
    )
