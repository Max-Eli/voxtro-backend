"""CORS middleware configuration"""
from fastapi.middleware.cors import CORSMiddleware
from app.config import get_settings

settings = get_settings()


def setup_cors(app):
    """
    Configure CORS middleware for the application

    Args:
        app: FastAPI application instance
    """
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
