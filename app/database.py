"""Database connection and utilities"""
from supabase import create_client, Client
from app.config import get_settings

settings = get_settings()

# Service role client (bypasses RLS - use carefully)
supabase_admin: Client = create_client(
    settings.supabase_url,
    settings.supabase_service_role_key
)


def get_supabase_client(auth_token: str = None) -> Client:
    """
    Get Supabase client with user auth context

    Args:
        auth_token: JWT token from user session

    Returns:
        Supabase client configured with user context
    """
    if auth_token:
        return create_client(
            settings.supabase_url,
            settings.supabase_anon_key,
            options={
                "global": {
                    "headers": {
                        "Authorization": f"Bearer {auth_token}"
                    }
                }
            }
        )
    return create_client(settings.supabase_url, settings.supabase_anon_key)
