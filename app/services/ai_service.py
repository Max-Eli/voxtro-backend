"""AI service for OpenAI integration"""
import hashlib
import httpx
from typing import List, Dict, Any, Optional
from app.config import get_settings
from app.database import supabase_admin
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)
settings = get_settings()


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

        # Clean up expired cache entries
        await supabase_admin.table("response_cache").delete().lt(
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
                "message": cached["response"],
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

        supabase_admin.table("response_cache").insert({
            "chatbot_id": chatbot_id,
            "question_hash": question_hash,
            "question": question[:500],  # Truncate for storage
            "response": response,
            "model": model,
            "expires_at": expires_at.isoformat(),
            "hit_count": 0
        }).execute()

        logger.info(f"Cached response for chatbot {chatbot_id}")
    except Exception as e:
        logger.error(f"Cache save error: {e}")


async def call_openai(
    messages: List[Dict[str, str]],
    model: str = "gpt-4o-mini",
    temperature: float = 0.7,
    max_tokens: int = 1000,
    tools: Optional[List[Dict]] = None
) -> Dict[str, Any]:
    """
    Call OpenAI API

    Args:
        messages: List of message objects with role and content
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
                    "Authorization": f"Bearer {settings.openai_api_key}",
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
            "total_tokens": input_tokens + output_tokens,
            "model": model,
            "cost": cost,
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
