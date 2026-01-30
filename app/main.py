"""Main FastAPI application"""
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from app.middleware.cors import setup_cors
from app.middleware.error_handler import ErrorHandlerMiddleware
from contextlib import asynccontextmanager
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

# APScheduler setup
scheduler = None

def setup_scheduler():
    """Initialize the background scheduler for automatic syncs"""
    global scheduler
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from app.routers.admin import run_scheduled_sync

        scheduler = BackgroundScheduler()

        # Run sync every 5 minutes
        scheduler.add_job(
            run_scheduled_sync,
            'interval',
            minutes=5,
            id='sync_all_conversations',
            name='Sync all conversations and generate AI summaries',
            replace_existing=True
        )

        scheduler.start()
        logger.info("Background scheduler started - syncing every 5 minutes")
    except Exception as e:
        logger.error(f"Failed to start scheduler: {e}")


def shutdown_scheduler():
    """Shutdown the scheduler gracefully"""
    global scheduler
    if scheduler:
        scheduler.shutdown(wait=False)
        logger.info("Background scheduler stopped")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events"""
    # Startup
    setup_scheduler()
    yield
    # Shutdown
    shutdown_scheduler()


# Create FastAPI app with lifespan
app = FastAPI(
    title="Voxtro API",
    description="AI-powered customer engagement platform API",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# Setup CORS
setup_cors(app)

# Add error handling middleware
app.add_middleware(ErrorHandlerMiddleware)

# Health check endpoint
@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "voxtro-backend", "scheduler": "running" if scheduler and scheduler.running else "stopped"}

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "message": "Voxtro Backend API",
        "version": "2.0.0",
        "docs": "/docs"
    }

# Import and include routers
from app.routers import chat, widget, voice, whatsapp, webhooks, notifications, customers, forms, leads, openai_connection, admin, permissions

app.include_router(chat.router, prefix="/api/chat", tags=["Chat"])
app.include_router(widget.router, prefix="/api/widget", tags=["Widget"])
app.include_router(voice.router, prefix="/api/voice", tags=["Voice Assistants"])
app.include_router(whatsapp.router, prefix="/api/whatsapp", tags=["WhatsApp Agents"])
app.include_router(webhooks.router, prefix="/api/webhooks", tags=["Webhooks"])
app.include_router(notifications.router, prefix="/api/notifications", tags=["Notifications"])
app.include_router(customers.router, prefix="/api/customers", tags=["Customers"])
app.include_router(permissions.router, prefix="/api/permissions", tags=["Permissions"])
app.include_router(forms.router, prefix="/api/forms", tags=["Forms"])
app.include_router(leads.router, prefix="/api/leads", tags=["Leads"])
app.include_router(openai_connection.router, prefix="/api/openai", tags=["OpenAI Connection"])
app.include_router(admin.router, prefix="/api/admin", tags=["Admin"])

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
