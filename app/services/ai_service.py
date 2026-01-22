"""AI service for OpenAI integration"""
import hashlib
import httpx
from typing import List, Dict, Any, Optional
from fastapi import HTTPException
from app.config import get_settings
from app.database import supabase_admin
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)
settings = get_settings()


async def get_user_openai_key(user_id: str, allow_fallback: bool = True) -> str:
    """
    Get user's OpenAI API key from openai_connections table
    Falls back to server-side key if user hasn't configured their own

    Args:
        user_id: User's UUID
        allow_fallback: If True, use server-side key as fallback for migration period

    Returns:
        User's OpenAI API key or fallback server key

    Raises:
        HTTPException: If no active API key found and fallback disabled
    """
    try:
        result = supabase_admin.table('openai_connections').select('api_key').eq(
            'user_id', user_id
        ).eq('is_active', True).single().execute()

        if result.data and result.data.get('api_key'):
            return result.data['api_key']

        # User hasn't configured their key yet
        if allow_fallback and settings.openai_api_key:
            logger.info(f"Using fallback OpenAI key for user {user_id} (migration period)")
            return settings.openai_api_key

        raise HTTPException(
            status_code=400,
            detail="No OpenAI API key configured. Please add your API key in Settings."
        )

    except HTTPException:
        raise
    except Exception as e:
        # No user key found, try fallback
        if allow_fallback and settings.openai_api_key:
            logger.info(f"Using fallback OpenAI key for user {user_id} (migration period)")
            return settings.openai_api_key

        logger.error(f"Error fetching OpenAI key for user {user_id}: {e}")
        raise HTTPException(
            status_code=400,
            detail="No OpenAI API key configured. Please add your API key in Settings."
        )


def estimate_tokens(text: str) -> int:
    """Rough estimation: 1 token ≈ 4 characters"""
    return len(text) // 4


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Calculate API cost based on model and token usage"""
    pricing = {
        'gpt-4o-mini': {'input': 0.00015, 'output': 0.0006},
        'gpt-4o': {'input': 0.0025, 'output': 0.01},
        'gpt-4': {'input': 0.03, 'output': 0.06},
        'gpt-3.5-turbo': {'input': 0.0015, 'output': 0.002},
    }

    rates = pricing.get(model, pricing['gpt-4o-mini'])
    return ((input_tokens * rates['input']) + (output_tokens * rates['output'])) / 1000


def create_question_hash(question: str) -> str:
    """Create hash for cache key"""
    normalized = question.lower().strip()
    return hashlib.md5(normalized.encode()).hexdigest()


async def check_cache(chatbot_id: str, question: str) -> Optional[Dict[str, Any]]:
    """Check if response is cached"""
    try:
        question_hash = create_question_hash(question)

        # Clean up expired cache entries (sync call, no await)
        supabase_admin.table("response_cache").delete().lt(
            "expires_at", datetime.utcnow().isoformat()
        ).execute()

        # Look for cached response
        result = supabase_admin.table("response_cache")\
            .select("*")\
            .eq("chatbot_id", chatbot_id)\
            .eq("question_hash", question_hash)\
            .gt("expires_at", datetime.utcnow().isoformat())\
            .order("created_at", desc=True)\
            .limit(1)\
            .execute()

        if result.data and len(result.data) > 0:
            cached = result.data[0]

            # Update hit count
            supabase_admin.table("response_cache").update({
                "hit_count": cached["hit_count"] + 1
            }).eq("id", cached["id"]).execute()

            logger.info(f"Cache hit for chatbot {chatbot_id}")
            return {
                "message": cached["response_text"],
                "cache_hit": True
            }

        return None
    except Exception as e:
        logger.error(f"Cache check error: {e}")
        return None


async def save_to_cache(chatbot_id: str, question: str, response: str, model: str, duration_hours: int = 168):
    """Save response to cache"""
    try:
        question_hash = create_question_hash(question)
        expires_at = datetime.utcnow() + timedelta(hours=duration_hours)
        input_tokens = estimate_tokens(question)
        output_tokens = estimate_tokens(response)

        supabase_admin.table("response_cache").insert({
            "chatbot_id": chatbot_id,
            "question_hash": question_hash,
            "question_text": question[:500],  # Truncate for storage
            "response_text": response,
            "model_used": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "expires_at": expires_at.isoformat(),
            "hit_count": 0
        }).execute()

        logger.info(f"Cached response for chatbot {chatbot_id}")
    except Exception as e:
        logger.error(f"Cache save error: {e}")


async def call_openai(
    messages: List[Dict[str, str]],
    api_key: str,
    model: str = "gpt-4o-mini",
    temperature: float = 0.7,
    max_tokens: int = 1000,
    tools: Optional[List[Dict]] = None
) -> Dict[str, Any]:
    """
    Call OpenAI API with user-provided API key

    Args:
        messages: List of message objects with role and content
        api_key: User's OpenAI API key
        model: Model name
        temperature: Sampling temperature
        max_tokens: Max tokens to generate
        tools: Optional function calling tools

    Returns:
        Dict with response and token usage
    """
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            payload = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens
            }

            if tools:
                payload["tools"] = tools
                payload["tool_choice"] = "auto"

            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                },
                json=payload
            )

            if response.status_code != 200:
                error_text = response.text
                logger.error(f"OpenAI API error: {error_text}")
                raise Exception(f"OpenAI API error: {response.status_code}")

            data = response.json()

            return {
                "message": data["choices"][0]["message"]["content"],
                "tool_calls": data["choices"][0]["message"].get("tool_calls"),
                "usage": data.get("usage", {}),
                "model": data.get("model")
            }
    except Exception as e:
        logger.error(f"OpenAI call failed: {e}")
        raise


async def track_token_usage(
    chatbot_id: str,
    conversation_id: Optional[str],
    input_tokens: int,
    output_tokens: int,
    model: str,
    cache_hit: bool = False
):
    """Track token usage for billing and limits"""
    try:
        cost = calculate_cost(model, input_tokens, output_tokens)

        supabase_admin.table("token_usage").insert({
            "chatbot_id": chatbot_id,
            "conversation_id": conversation_id,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "model_used": model,
            "total_cost": cost,
            "cache_hit": cache_hit
        }).execute()

        logger.info(f"Tracked {input_tokens + output_tokens} tokens for chatbot {chatbot_id}")
    except Exception as e:
        logger.error(f"Token tracking error: {e}")


async def check_token_limits(chatbot_id: str, daily_limit: int, monthly_limit: int) -> Optional[str]:
    """
    Check if token limits are exceeded

    Returns:
        Error message if limit exceeded, None otherwise
    """
    try:
        now = datetime.utcnow()
        today_start = datetime(now.year, now.month, now.day)
        month_start = datetime(now.year, now.month, 1)

        # Check daily usage
        daily_result = supabase_admin.table("token_usage")\
            .select("input_tokens, output_tokens")\
            .eq("chatbot_id", chatbot_id)\
            .gte("created_at", today_start.isoformat())\
            .execute()

        daily_tokens = sum(
            u["input_tokens"] + u["output_tokens"]
            for u in (daily_result.data or [])
        )

        if daily_tokens >= daily_limit:
            return "Daily token limit reached. Please try again tomorrow."

        # Check monthly usage
        monthly_result = supabase_admin.table("token_usage")\
            .select("input_tokens, output_tokens")\
            .eq("chatbot_id", chatbot_id)\
            .gte("created_at", month_start.isoformat())\
            .execute()

        monthly_tokens = sum(
            u["input_tokens"] + u["output_tokens"]
            for u in (monthly_result.data or [])
        )

        if monthly_tokens >= monthly_limit:
            return "Monthly token limit reached. Please upgrade your plan."

        return None
    except Exception as e:
        logger.error(f"Token limit check error: {e}")
        return None  # Don't block on error


async def extract_lead_info(messages: List[Dict[str, str]], api_key: str) -> Optional[Dict[str, Any]]:
    """
    Extract lead information from conversation messages using OpenAI

    Args:
        messages: List of conversation messages with role and content
        api_key: User's OpenAI API key

    Returns:
        Dict with extracted lead info or None if no lead detected
    """
    try:
        # Build conversation text
        conversation_text = "\n".join([
            f"{msg['role']}: {msg['content']}"
            for msg in messages
        ])

        # Create extraction prompt
        extraction_prompt = f"""
        Analyze the following conversation and extract any lead information.
        Look for: name, email, phone number, company name, and any relevant notes about their needs or interests.

        Conversation:
        {conversation_text}

        Extract the information in JSON format with these fields:
        - name: Full name if mentioned
        - email: Email address if mentioned
        - phone: Phone number if mentioned
        - company: Company name if mentioned
        - notes: Brief summary of their needs or interests

        If no lead information is found, respond with: {{"no_lead": true}}

        Respond ONLY with valid JSON, no additional text.
        """

        response = await call_openai(
            messages=[{"role": "user", "content": extraction_prompt}],
            api_key=api_key,
            model="gpt-4o-mini",
            temperature=0.3,
            max_tokens=500
        )

        # Parse the JSON response
        import json
        lead_data = json.loads(response["message"])

        if lead_data.get("no_lead"):
            return None

        # Only return if we have at least one useful field
        if lead_data.get("email") or lead_data.get("phone") or lead_data.get("name"):
            return lead_data

        return None

    except Exception as e:
        logger.error(f"Lead extraction error: {e}")
        return None
