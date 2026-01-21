# Voxtro Backend API

FastAPI backend for the Voxtro AI-powered customer engagement platform - enabling businesses to build chatbots, voice assistants, and WhatsApp agents.

## Features

- **AI Chatbot Builder** - Create and customize chat widgets for websites
- **Voice Assistants** - VAPI integration for voice-based customer interactions
- **WhatsApp Agents** - ElevenLabs integration for WhatsApp automation
- **Customer Portal** - Customer authentication and support ticketing
- **Lead Extraction** - AI-powered lead extraction from conversations
- **Website Crawling** - Extract content from websites to train chatbots
- **Email Notifications** - Resend integration for transactional emails
- **Multi-tenant Architecture** - Each user manages their own chatbots and integrations

## Technology Stack

- **Framework**: FastAPI 0.115.6
- **Python**: 3.11.9
- **Database**: Supabase (PostgreSQL)
- **AI**: OpenAI API (GPT-4o-mini)
- **Email**: Resend
- **Deployment**: Render.com

## Project Structure

```
voxtro-backend/
├── app/
│   ├── main.py              # FastAPI application entry
│   ├── config.py            # Configuration and settings
│   ├── database.py          # Supabase client setup
│   ├── routers/             # API route handlers
│   │   ├── chat.py          # Chat and conversation endpoints
│   │   ├── voice.py         # VAPI voice assistant endpoints
│   │   ├── whatsapp.py      # ElevenLabs WhatsApp endpoints
│   │   ├── customers.py     # Customer management
│   │   ├── notifications.py # Email notifications
│   │   ├── forms.py         # Form submissions
│   │   └── leads.py         # Lead extraction
│   ├── models/              # Pydantic models
│   ├── middleware/          # CORS, auth, error handling
│   └── services/            # Business logic services
├── requirements.txt         # Python dependencies
├── runtime.txt              # Python version (3.11.9)
├── render.yaml              # Render deployment config
└── .env.example             # Environment variable template
```

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` with your credentials:
```env
SUPABASE_URL=https://nzqzmvsrsfynatxojuil.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
SUPABASE_ANON_KEY=your-anon-key
SUPABASE_JWT_SECRET=your-jwt-secret
OPENAI_API_KEY=your-openai-key
RESEND_API_KEY=your-resend-key
ENVIRONMENT=development
```

### 3. Run Development Server

```bash
uvicorn app.main:app --reload --port 8000
```

### 4. Access API Documentation

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
- **Health Check**: http://localhost:8000/health

## API Endpoints

### Chat & Chatbots
- `POST /api/chat/message` - Handle chat messages with AI responses
- `POST /api/chat/crawl` - Crawl website for chatbot knowledge
- `GET /api/widget/config/{chatbot_id}` - Get chatbot widget configuration

### Voice Assistants (VAPI)
- `POST /api/voice/sync` - Sync voice assistants from user's VAPI account
- `PATCH /api/voice/{assistant_id}` - Update voice assistant
- `POST /api/voice/validate` - Validate VAPI API key
- `GET /api/voice/token` - Get VAPI web token for calls

### WhatsApp Agents (ElevenLabs)
- `POST /api/whatsapp/sync` - Sync WhatsApp agents from ElevenLabs
- `PATCH /api/whatsapp/{agent_id}` - Update WhatsApp agent
- `GET /api/whatsapp/{agent_id}` - Get WhatsApp agent details
- `POST /api/whatsapp/validate-elevenlabs` - Validate ElevenLabs API key

### Customers
- `POST /api/customers` - Create customer with authentication
- `POST /api/customers/tickets` - Create support ticket
- `POST /api/customers/send-login-link` - Send magic login link
- `POST /api/customers/extract-leads` - Extract leads from conversations

### Notifications
- `POST /api/notifications/email` - Send email via Resend
- `POST /api/notifications/contact` - Handle contact form submissions
- `POST /api/notifications/ticket-reply` - Send ticket reply notification
- `POST /api/notifications/admin-ticket` - Send admin ticket notification

### Forms
- `POST /api/forms/submit` - Handle form submissions from chatbots

### Leads
- `POST /api/leads/extract` - Extract lead information from conversation

## Database Schema

Supabase tables:
- `chatbots` - User-created chatbots (21 migrated)
- `customers` - End-user customers (5 migrated)
- `actions` - Chatbot actions/workflows (10 migrated)
- `faqs` - FAQ knowledge base (26 migrated)
- `voice_assistants` - VAPI voice assistant configurations
- `whatsapp_agents` - ElevenLabs WhatsApp agents
- `conversations` - Chat conversations between visitors and chatbots
- `messages` - Individual chat messages
- `support_tickets` - Customer support tickets
- `leads` - Extracted lead information
- `profiles` - User profiles (app builders)
- `voice_connections` - User VAPI API keys (multi-tenant)
- `elevenlabs_connections` - User ElevenLabs API keys (multi-tenant)

## Deployment

### Render.com Deployment

See [DEPLOYMENT.md](DEPLOYMENT.md) for detailed deployment instructions.

**Quick Deploy:**

1. Push to GitHub
2. Connect GitHub repo to Render
3. Add environment variables in Render dashboard
4. Deploy automatically

The app will deploy with:
- Python 3.11.9 (specified in `runtime.txt`)
- All dependencies from `requirements.txt`
- Health check at `/health`

### Environment Variables Required

```bash
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY
SUPABASE_ANON_KEY
SUPABASE_JWT_SECRET
OPENAI_API_KEY
RESEND_API_KEY
ENVIRONMENT=production
```

## Multi-Tenant Architecture

Voxtro is a multi-tenant SaaS application:

- **App Users** (builders) create chatbots, voice assistants, and WhatsApp agents
- **End Customers** interact with chatbots via the customer portal
- Each user provides their own VAPI and ElevenLabs API keys
- Keys are securely stored in user-specific connection tables
- Only OpenAI and Resend keys are backend environment variables

## Migration Status

Successfully migrated from old Supabase project to new project:
- ✅ 21 chatbots migrated
- ✅ 5 customers migrated
- ✅ 10 actions migrated
- ✅ 26 FAQs migrated
- ✅ All frontend API calls updated
- ✅ Backend deployed to Render

## Development

### Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run with hot reload
uvicorn app.main:app --reload

# Run on specific port
uvicorn app.main:app --reload --port 8080
```

### Testing

```bash
# Run tests (when test suite is available)
pytest tests/ -v
```

## Troubleshooting

### Build Errors on Render
- Ensure `runtime.txt` has Python 3.11.9
- All packages now have prebuilt wheels (no Rust compilation needed)

### Database Connection Issues
- Verify Supabase credentials in environment variables
- Check Supabase project is active
- Ensure service role key has correct permissions

### API Errors
- Check logs in Render dashboard
- Verify all required environment variables are set
- Test health endpoint: `curl https://your-app.onrender.com/health`

## Support & Issues

- **GitHub**: [voxtro-backend](https://github.com/Max-Eli/voxtro-backend)
- **Documentation**: See `DEPLOYMENT.md` for detailed deployment guide

## License

Proprietary - All rights reserved
