"""Chat endpoints - Main conversation handling"""
from fastapi import APIRouter, HTTPException, Depends
from typing import Optional, List, Dict, Any
import uuid
from datetime import datetime
import logging

from app.models.chat import (
    ChatMessageRequest,
    ChatMessageResponse,
    WebsiteCrawlRequest,
    WebsiteCrawlResponse
)
from app.middleware.auth import get_optional_user
from app.database import supabase_admin
from app.services.ai_service import (
    call_openai,
    check_cache,
    save_to_cache,
    track_token_usage,
    check_token_limits,
    estimate_tokens,
    get_user_openai_key
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/message", response_model=ChatMessageResponse)
async def handle_chat_message(
    request: ChatMessageRequest,
    auth_data: Optional[Dict] = Depends(get_optional_user)
):
    """
    Main chat endpoint - handles conversation and AI responses
    Replaces: chat, inline-chat, messenger edge functions
    """
    try:
        # Get chatbot configuration
        chatbot_query = supabase_admin.table("chatbots").select(
            "*, daily_token_limit, monthly_token_limit, cache_enabled, cache_duration_hours"
        ).eq("id", request.chatbot_id)

        if not request.preview_mode:
            chatbot_query = chatbot_query.eq("is_active", True)

        chatbot_result = chatbot_query.single().execute()

        if not chatbot_result.data:
            raise HTTPException(status_code=404, detail="Chatbot not found or inactive")

        chatbot = chatbot_result.data

        # Get user's OpenAI API key
        user_id = chatbot.get("user_id")
        if not user_id:
            raise HTTPException(status_code=500, detail="Chatbot configuration error")

        openai_api_key = await get_user_openai_key(user_id)

        # Get chatbot actions
        actions_result = supabase_admin.table("chatbot_actions").select(
            "id, action_type, name, description, configuration, is_active"
        ).eq("chatbot_id", request.chatbot_id).eq("is_active", True).execute()

        chatbot["actions"] = actions_result.data or []

        # Get or create conversation (skip if preview mode)
        conversation_id = request.conversation_id
        is_new_conversation = False

        if not request.preview_mode:
            if not conversation_id:
                # Try to find existing conversation
                existing_conv = supabase_admin.table("conversations").select("id").eq(
                    "chatbot_id", request.chatbot_id
                ).eq("visitor_id", request.visitor_id).neq("status", "ended").execute()

                if existing_conv.data and len(existing_conv.data) > 0:
                    conversation_id = existing_conv.data[0]["id"]
                else:
                    # Create new conversation
                    new_conv = supabase_admin.table("conversations").insert({
                        "id": str(uuid.uuid4()),
                        "chatbot_id": request.chatbot_id,
                        "visitor_id": request.visitor_id,
                        "status": "active"
                    }).execute()
                    conversation_id = new_conv.data[0]["id"]
                    is_new_conversation = True

            # Save user message
            supabase_admin.table("messages").insert({
                "conversation_id": conversation_id,
                "role": "user",
                "content": request.message
            }).execute()

            # Check token limits
            if chatbot.get("daily_token_limit") and chatbot.get("monthly_token_limit"):
                limit_error = await check_token_limits(
                    request.chatbot_id,
                    chatbot["daily_token_limit"],
                    chatbot["monthly_token_limit"]
                )
                if limit_error:
                    raise HTTPException(status_code=429, detail=limit_error)

        # Check cache (only for first messages, skip if preview or has history)
        cached_response = None
        if not request.preview_mode and chatbot.get("cache_enabled"):
            # Check if this is first message
            if conversation_id:
                msg_count = supabase_admin.table("messages").select(
                    "id", count="exact"
                ).eq("conversation_id", conversation_id).execute()

                if msg_count.count <= 1:  # Only user message
                    cached_response = await check_cache(request.chatbot_id, request.message)

        if cached_response:
            # Return cached response
            if not request.preview_mode and conversation_id:
                supabase_admin.table("messages").insert({
                    "conversation_id": conversation_id,
                    "role": "assistant",
                    "content": cached_response["message"]
                }).execute()

            return ChatMessageResponse(
                conversation_id=conversation_id or "preview",
                message=cached_response["message"],
                actions=[]
            )

        # Load conversation history (last 20 messages)
        conversation_history = []
        if not request.preview_mode and conversation_id:
            history_result = supabase_admin.table("messages").select(
                "role, content"
            ).eq("conversation_id", conversation_id).order(
                "created_at", desc=False
            ).limit(20).execute()

            conversation_history = [
                {"role": msg["role"], "content": msg["content"]}
                for msg in (history_result.data or [])
            ]

        # Prepare messages for OpenAI
        messages = [
            {"role": "system", "content": chatbot.get("system_prompt", "You are a helpful assistant.")}
        ]

        # Add conversation history or just current message
        if conversation_history:
            messages.extend(conversation_history)
        else:
            messages.append({"role": "user", "content": request.message})

        # Prepare tools if actions exist
        tools = None
        if chatbot["actions"]:
            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": action["name"],
                        "description": action["description"],
                        "parameters": action["configuration"].get("parameters", {})
                    }
                }
                for action in chatbot["actions"]
            ]

        # Call OpenAI with user's API key
        ai_response = await call_openai(
            messages=messages,
            api_key=openai_api_key,
            model=chatbot.get("model", "gpt-4o-mini"),
            temperature=chatbot.get("temperature", 0.7),
            max_tokens=chatbot.get("max_tokens", 1000),
            tools=tools
        )

        response_message = ai_response["message"]
        usage = ai_response.get("usage", {})

        # Save assistant message (skip if preview)
        if not request.preview_mode and conversation_id:
            supabase_admin.table("messages").insert({
                "conversation_id": conversation_id,
                "role": "assistant",
                "content": response_message
            }).execute()

            # Track token usage
            await track_token_usage(
                chatbot_id=request.chatbot_id,
                conversation_id=conversation_id,
                input_tokens=usage.get("prompt_tokens", estimate_tokens(str(messages))),
                output_tokens=usage.get("completion_tokens", estimate_tokens(response_message)),
                model=chatbot.get("model", "gpt-4o-mini"),
                cache_hit=False
            )

            # Cache response if enabled and first message
            if chatbot.get("cache_enabled") and len(conversation_history) <= 1:
                await save_to_cache(
                    chatbot_id=request.chatbot_id,
                    question=request.message,
                    response=response_message,
                    model=chatbot.get("model", "gpt-4o-mini"),
                    duration_hours=chatbot.get("cache_duration_hours", 168)
                )

        return ChatMessageResponse(
            conversation_id=conversation_id or "preview",
            message=response_message,
            actions=ai_response.get("tool_calls")
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Chat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/crawl", response_model=WebsiteCrawlResponse)
async def crawl_website(
    request: WebsiteCrawlRequest,
    auth_data: Dict = Depends(get_optional_user)
):
    """
    Crawl website for chatbot knowledge
    Replaces: crawl-website edge function
    """
    try:
        # Import crawler service
        from app.services.crawler_service import crawl_and_extract

        result = await crawl_and_extract(
            url=request.url,
            max_pages=request.max_pages
        )

        # Save crawled content to chatbot (you'd store this in a knowledge base table)
        # For now, just return success

        return WebsiteCrawlResponse(
            success=True,
            pages_crawled=result["pages_crawled"],
            content_extracted=result["content_length"]
        )

    except Exception as e:
        logger.error(f"Crawl error: {e}")
        return WebsiteCrawlResponse(
            success=False,
            pages_crawled=0,
            content_extracted=0,
            error=str(e)
        )
