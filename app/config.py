"""Application configuration"""
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings"""

    # Supabase
    supabase_url: str
    supabase_service_role_key: str
    supabase_anon_key: str
    supabase_jwt_secret: str = ""  # No longer needed - kept for backwards compatibility

    # External APIs
    # NOTE: OpenAI API keys are now user-provided (multi-tenant)
    # Server-side key used as fallback during migration period
    openai_api_key: str
    resend_api_key: str

    # Application
    environment: str = "development"
    cors_origins: list[str] = ["*"]
    
    # Frontend URLs (for email links, redirects, etc.)
    frontend_url: str = "https://dev.voxtro.io"
    support_email: str = "support@voxtro.io"

    class Config:
        env_file = ".env"
        case_sensitive = False


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance"""
    return Settings()
