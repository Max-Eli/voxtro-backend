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


async def execute_tool_action(action: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    """
    Execute a chatbot action/tool with the provided arguments

    Args:
        action: Action configuration from chatbot_actions table
        arguments: Arguments passed from OpenAI tool call

    Returns:
        String result of the action execution
    """
    try:
        import httpx
        import json

        action_type = action.get("action_type", "api")
        config = action.get("configuration", {})

        if action_type == "api":
            # Execute API call
            url = config.get("url", "")
            method = config.get("method", "GET").upper()
            headers = config.get("headers", {})

            # Replace parameters in URL and body
            for param_name, param_value in arguments.items():
                url = url.replace(f"{{{{{param_name}}}}}", str(param_value))

            async with httpx.AsyncClient(timeout=30.0) as client:
                if method == "GET":
                    response = await client.get(url, headers=headers)
                elif method == "POST":
                    body = config.get("body", {})
                    # Replace parameters in body
                    body_str = json.dumps(body)
                    for param_name, param_value in arguments.items():
                        body_str = body_str.replace(f"{{{{{param_name}}}}}", str(param_value))
                    body = json.loads(body_str)
                    response = await client.post(url, json=body, headers=headers)
                elif method == "PUT":
                    body = config.get("body", {})
                    body_str = json.dumps(body)
                    for param_name, param_value in arguments.items():
                        body_str = body_str.replace(f"{{{{{param_name}}}}}", str(param_value))
                    body = json.loads(body_str)
                    response = await client.put(url, json=body, headers=headers)
                elif method == "DELETE":
                    response = await client.delete(url, headers=headers)
                else:
                    return f"Unsupported HTTP method: {method}"

                if response.status_code >= 200 and response.status_code < 300:
                    try:
                        return json.dumps(response.json())
                    except:
                        return response.text
                else:
                    return f"API call failed with status {response.status_code}: {response.text}"

        elif action_type == "webhook":
            # Execute webhook
            url = config.get("url", "")
            headers = config.get("headers", {})

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    url,
                    json=arguments,
                    headers=headers
                )

                if response.status_code >= 200 and response.status_code < 300:
                    try:
                        return json.dumps(response.json())
                    except:
                        return response.text or "Webhook executed successfully"
                else:
                    return f"Webhook failed with status {response.status_code}: {response.text}"

        else:
            return f"Unsupported action type: {action_type}"

    except Exception as e:
        logger.error(f"Tool execution error: {e}")
        return f"Error executing tool: {str(e)}"


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
        # Get chatbot configuration including knowledge base
        chatbot_query = supabase_admin.table("chatbots").select(
            "*, daily_token_limit, monthly_token_limit, cache_enabled, cache_duration_hours, knowledge_base"
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

        # Build system prompt with knowledge base if available
        system_prompt = chatbot.get("system_prompt", "You are a helpful assistant.")
        
        # Add knowledge base content to system prompt if available
        knowledge_base = chatbot.get("knowledge_base")
        if knowledge_base:
            system_prompt += f"\n\n### Knowledge Base (Use this information to answer questions):\n{knowledge_base[:8000]}"  # Limit to avoid token overflow

        # Add FAQs to context if available
        faqs_result = supabase_admin.table("chatbot_faqs").select(
            "question, answer"
        ).eq("chatbot_id", request.chatbot_id).eq("is_active", True).limit(20).execute()
        
        if faqs_result.data:
            faq_text = "\n".join([
                f"Q: {faq['question']}\nA: {faq['answer']}"
                for faq in faqs_result.data
            ])
            system_prompt += f"\n\n### Frequently Asked Questions:\n{faq_text}"

        # Prepare messages for OpenAI
        messages = [
            {"role": "system", "content": system_prompt}
        ]

        # Add conversation history or just current message
        if conversation_history:
            messages.extend(conversation_history)
        else:
            messages.append({"role": "user", "content": request.message})

        # Prepare tools if actions exist
        tools = None
        if chatbot["actions"]:
            tools = []
            for action in chatbot["actions"]:
                config = action.get("configuration", {})
                params = config.get("parameters", {})
                
                # Convert array-style parameters to JSON Schema object
                if isinstance(params, list):
                    # Convert from [{name, type, required, description}, ...] to JSON Schema
                    properties = {}
                    required = []
                    for param in params:
                        param_name = param.get("name", "")
                        param_type = param.get("type", "string")
                        # Map common types to JSON Schema types
                        type_map = {"text": "string", "number": "number", "boolean": "boolean", "integer": "integer"}
                        json_type = type_map.get(param_type, "string")
                        
                        properties[param_name] = {
                            "type": json_type,
                            "description": param.get("description", "")
                        }
                        if param.get("required", False):
                            required.append(param_name)
                    
                    params = {
                        "type": "object",
                        "properties": properties,
                        "required": required
                    }
                elif not params:
                    # Empty parameters - provide valid empty schema
                    params = {"type": "object", "properties": {}}
                
                tools.append({
                    "type": "function",
                    "function": {
                        "name": action["name"],
                        "description": action.get("description", ""),
                        "parameters": params
                    }
                })

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
        tool_calls = ai_response.get("tool_calls")
        usage = ai_response.get("usage", {})

        # Handle tool calls if present
        if tool_calls:
            # Add the assistant's tool call message to the conversation
            messages.append({
                "role": "assistant",
                "content": response_message,
                "tool_calls": tool_calls
            })

            # Execute each tool and collect results
            for tool_call in tool_calls:
                function_name = tool_call["function"]["name"]
                function_args = tool_call["function"]["arguments"]

                # Parse arguments if they're a string
                import json
                if isinstance(function_args, str):
                    function_args = json.loads(function_args)

                # Find the matching action
                action = next((a for a in chatbot["actions"] if a["name"] == function_name), None)

                if action:
                    # Execute the action based on type
                    tool_result = await execute_tool_action(action, function_args)
                else:
                    tool_result = f"Error: Function {function_name} not found"

                # Add tool result to messages
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": tool_result
                })

            # Call OpenAI again with tool results to get final response
            final_response = await call_openai(
                messages=messages,
                api_key=openai_api_key,
                model=chatbot.get("model", "gpt-4o-mini"),
                temperature=chatbot.get("temperature", 0.7),
                max_tokens=chatbot.get("max_tokens", 1000),
                tools=tools
            )

            response_message = final_response["message"]
            # Add final response usage to total
            final_usage = final_response.get("usage", {})
            usage["prompt_tokens"] = usage.get("prompt_tokens", 0) + final_usage.get("prompt_tokens", 0)
            usage["completion_tokens"] = usage.get("completion_tokens", 0) + final_usage.get("completion_tokens", 0)

        # Save assistant message (skip if preview) - only save if there's actual content
        if not request.preview_mode and conversation_id and response_message:
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
            message=response_message or "Processing...",
            actions=tool_calls
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
        # Verify chatbot ownership if authenticated
        if auth_data:
            chatbot_check = supabase_admin.table("chatbots").select("id, user_id").eq(
                "id", request.chatbot_id
            ).single().execute()
            
            if not chatbot_check.data:
                raise HTTPException(status_code=404, detail="Chatbot not found")
            
            if chatbot_check.data.get("user_id") != auth_data.get("user_id"):
                raise HTTPException(status_code=403, detail="Access denied")

        # Import crawler service
        from app.services.crawler_service import crawl_and_extract

        result = await crawl_and_extract(
            url=request.url,
            max_pages=request.max_pages
        )

        # Save crawled content to chatbot's knowledge base
        crawled_content = result.get("content", "")
        
        # Update chatbot with new knowledge base content
        # Append to existing knowledge base if it exists
        existing_chatbot = supabase_admin.table("chatbots").select(
            "knowledge_base"
        ).eq("id", request.chatbot_id).single().execute()
        
        existing_kb = existing_chatbot.data.get("knowledge_base") or "" if existing_chatbot.data else ""
        
        # Combine existing and new content (with separator)
        new_knowledge_base = existing_kb
        if new_knowledge_base:
            new_knowledge_base += f"\n\n--- Content from {request.url} ---\n"
        new_knowledge_base += crawled_content[:50000]  # Limit content size
        
        # Save to chatbot
        supabase_admin.table("chatbots").update({
            "knowledge_base": new_knowledge_base
        }).eq("id", request.chatbot_id).execute()

        return WebsiteCrawlResponse(
            success=True,
            pages_crawled=result["pages_crawled"],
            content_extracted=result["content_length"]
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Crawl error: {e}")
        return WebsiteCrawlResponse(
            success=False,
            pages_crawled=0,
            content_extracted=0,
            error=str(e)
        )
