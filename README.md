# Voxtro Backend API

FastAPI backend for the Voxtro AI customer engagement platform.

## Features

- AI Chatbot management and conversation handling
- Voice Assistant integration (VAPI)
- WhatsApp Agent management (ElevenLabs)
- Customer management and authentication
- Email notifications and webhooks
- Lead extraction and analytics

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Copy environment file:
```bash
cp .env.example .env
```

3. Configure environment variables in `.env`

4. Run development server:
```bash
uvicorn app.main:app --reload --port 8000
```

## API Documentation

Once running, visit:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## Deployment

Configured for deployment on Render. See `render.yaml` for configuration.

## Testing

```bash
pytest tests/
```
