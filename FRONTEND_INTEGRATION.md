# Voxtro Backend API Integration Guide

## Base URL
```
Production: https://voxtro-backend.onrender.com
```

## Authentication

All authenticated endpoints require a **Supabase JWT token** in the Authorization header:
```
Authorization: Bearer <supabase_access_token>
```

The frontend gets this token from Supabase Auth (`supabase.auth.getSession()`).

There are **two user types**:
- **Admin Users** (business owners) - Can create chatbots, manage customers, etc.
- **Customers** - End users who access the customer portal

---

## 1. CHATBOT SYSTEM

### How Chatbots Work

1. **Admin creates a chatbot** in the frontend (saved directly to Supabase `chatbots` table)
2. **Chatbot has**: `system_prompt`, `model`, `temperature`, `max_tokens`, `knowledge_base`, etc.
3. **Widget embed code** points to: `https://voxtro-backend.onrender.com/api/widget/{chatbot_id}/script.js`
4. **When visitors chat**, messages go through the backend which:
   - Loads chatbot config + system prompt
   - Loads knowledge base content (from crawled websites)
   - Loads FAQs from `chatbot_faqs` table
   - Loads conversation history
   - Calls OpenAI with user's API key
   - Saves messages and tracks token usage

### Chat Endpoints

#### `POST /api/chat/message`
Main chat endpoint for conversations.

```json
// Request
{
  "chatbot_id": "uuid",
  "conversation_id": "uuid or null",  // null for new conversation
  "visitor_id": "unique_visitor_id",
  "message": "Hello!",
  "preview_mode": false  // true for testing in builder
}

// Response
{
  "conversation_id": "uuid",
  "message": "AI response text",
  "actions": []  // Tool calls if any
}
```

#### `POST /api/chat/crawl` (Auth Required)
Crawl a website to add to chatbot's knowledge base.

```json
// Request
{
  "chatbot_id": "uuid",
  "url": "https://example.com",
  "max_pages": 10
}

// Response
{
  "success": true,
  "pages_crawled": 5,
  "content_extracted": 15000
}
```
This saves the crawled content to the `knowledge_base` column in the `chatbots` table.

---

## 2. WIDGET EMBED SYSTEM

### Endpoints (PUBLIC - No Auth)

#### `GET /api/widget/{chatbot_id}/config`
Returns widget configuration for embedded chatbots.

```json
// Response
{
  "chatbot_id": "uuid",
  "name": "Support Bot",
  "theme": {
    "primary_color": "#6366f1",
    "secondary_color": "#ffffff",
    "position": "bottom-right",
    "avatar": "url"
  },
  "first_message": "Hi! How can I help?",
  "placeholder_text": "Type your message...",
  "forms": [...],
  "faqs": [...]
}
```

#### `POST /api/widget/{chatbot_id}/message`
Handle messages from embedded widget (no auth required).

```json
// Request
{
  "visitor_id": "visitor_123",
  "message": "Hello",
  "conversation_id": "uuid or null"
}
```

#### `GET /api/widget/{chatbot_id}/script.js`
Returns the JavaScript embed code. Customers embed this on their websites:

```html
<script src="https://voxtro-backend.onrender.com/api/widget/{chatbot_id}/script.js"></script>
```

---

## 3. OPENAI CONNECTION (User API Keys)

Users provide their own OpenAI API key for chatbots to use.

### Endpoints (Auth Required)

#### `POST /api/openai/connect`
Save user's OpenAI API key.

```json
// Request
{
  "api_key": "sk-..."
}
```

#### `GET /api/openai/validate`
Validate the stored API key works.

#### `GET /api/openai/status`
Check if user has connected their OpenAI key.

### Frontend Flow:
1. User goes to Settings → API Keys
2. User enters their OpenAI API key
3. Frontend calls `POST /api/openai/connect`
4. Key is stored in `openai_connections` table (encrypted)
5. All chatbots owned by this user will use their key

---

## 4. VOICE ASSISTANTS (VAPI Integration)

Users connect their VAPI account to sync voice assistants.

### Endpoints (Auth Required)

#### `POST /api/voice/validate`
Validate VAPI API key before saving.

```json
// Request
{
  "public_key": "vapi_public_key"
}

// Response
{
  "valid": true
}
```

#### `POST /api/voice/sync`
Sync voice assistants from user's VAPI account.

```json
// Response
{
  "count": 3,
  "assistants": [
    {
      "id": "vapi_assistant_id",
      "name": "Sales Assistant",
      "firstMessage": "Hello!",
      "voice": {...},
      "model": {...}
    }
  ]
}
```

#### `PATCH /api/voice/{assistant_id}`
Update a voice assistant.

#### `GET /api/voice/token`
Get VAPI web token for making calls from browser.

### Frontend Flow:
1. User goes to Voice Assistants page
2. User enters VAPI public key
3. Frontend calls `POST /api/voice/validate`
4. If valid, save to `voice_connections` table (frontend can do this directly via Supabase)
5. Frontend calls `POST /api/voice/sync` to import assistants
6. Assistants are stored in `voice_assistants` table

---

## 5. WHATSAPP AGENTS (ElevenLabs Integration)

Users connect their ElevenLabs account to sync WhatsApp agents.

### Endpoints (Auth Required)

#### `POST /api/whatsapp/validate-elevenlabs`
Validate ElevenLabs API key.

```json
// Request (query param)
?api_key=xi_api_key

// Response
{
  "valid": true,
  "user": { "subscription": {...} }
}
```

#### `POST /api/whatsapp/sync`
Sync agents from ElevenLabs.

```json
// Response
{
  "count": 2,
  "agents": [
    {
      "agent_id": "...",
      "name": "Support Agent"
    }
  ]
}
```

#### `PATCH /api/whatsapp/{agent_id}`
Update agent settings.

#### `GET /api/whatsapp/{agent_id}`
Get agent details.

### Frontend Flow:
1. User goes to WhatsApp Agents page
2. User enters ElevenLabs API key
3. Frontend calls `POST /api/whatsapp/validate-elevenlabs`
4. If valid, save to `elevenlabs_connections` table
5. Frontend calls `POST /api/whatsapp/sync` to import agents
6. Agents are stored in `whatsapp_agents` table

---

## 6. CUSTOMER MANAGEMENT

Admins can create customer accounts with portal access.

### Admin Endpoints (Auth Required - Admin Only)

#### `POST /api/customers`
Create a new customer with portal login.

```json
// Request
{
  "email": "customer@example.com",
  "password": "securepassword",
  "full_name": "John Doe",
  "company_name": "Acme Inc",
  "chatbot_id": "uuid"  // Optional - link to specific chatbot
}

// Response
{
  "customer_id": "uuid",
  "user_id": "supabase_auth_user_id",
  "email": "customer@example.com"
}
```

This creates:
1. A Supabase Auth user (with `is_customer: true` in metadata)
2. A record in `customers` table linked to the business owner

#### `POST /api/customers/send-login-link`
Send magic link to customer.

```json
// Request (query param)
?email=customer@example.com
```

#### `POST /api/customers/tickets`
Create support ticket for a customer.

```json
// Request
{
  "customer_id": "uuid",
  "subject": "Need help",
  "description": "Details...",
  "priority": "medium"
}
```

---

## 7. CUSTOMER PORTAL (For End Customers)

These endpoints are for **customers** (not admins) to access their portal.

### Customer Portal Endpoints (Auth Required - Customer Only)

#### `GET /api/customers/portal/me`
Get customer's own profile.

```json
// Response
{
  "id": "uuid",
  "user_id": "uuid",
  "email": "customer@example.com",
  "full_name": "John Doe",
  "company_name": "Acme Inc",
  "chatbot_id": "uuid or null",
  "created_by_user_id": "business_owner_uuid"
}
```

#### `GET /api/customers/portal/agents`
Get all agents/chatbots available to this customer.

```json
// Response
{
  "chatbots": [
    {
      "id": "uuid",
      "name": "Support Bot",
      "avatar_url": "...",
      "first_message": "Hi!",
      "is_active": true
    }
  ],
  "voice_assistants": [
    {
      "id": "uuid",
      "name": "Phone Support",
      "phone_number": "+1234567890"
    }
  ],
  "whatsapp_agents": [
    {
      "id": "uuid",
      "name": "WhatsApp Support",
      "status": "active"
    }
  ]
}
```

#### `GET /api/customers/portal/conversations`
Get customer's conversation history.

#### `GET /api/customers/portal/conversations/{conversation_id}/messages`
Get messages for a specific conversation.

#### `GET /api/customers/portal/tickets`
Get customer's support tickets.

#### `POST /api/customers/portal/tickets`
Create a new support ticket.

### Frontend Customer Portal Flow:
1. Customer logs in (via Supabase Auth - magic link or password)
2. Frontend detects `is_customer: true` in user metadata
3. Redirect to `/customer-portal` instead of main dashboard
4. Call `GET /api/customers/portal/agents` to show available agents
5. Customer can chat with chatbots, call voice assistants, view tickets

---

## 8. LEAD EXTRACTION

AI-powered extraction of leads from conversations.

### Endpoints (Auth Required)

#### `POST /api/leads/extract`
Extract leads from a single conversation.

```json
// Request (query param)
?conversation_id=uuid

// Response
{
  "success": true,
  "leads_found": 1,
  "lead_id": "uuid",
  "lead_info": {
    "name": "John Doe",
    "email": "john@example.com",
    "phone": "+1234567890",
    "company": "Acme Inc",
    "notes": "Interested in premium plan"
  }
}
```

#### `POST /api/leads/extract-batch`
Extract leads from multiple unprocessed conversations.

```json
// Request (query params)
?chatbot_id=uuid&limit=50

// Response
{
  "success": true,
  "leads_extracted": 5,
  "conversations_processed": 20,
  "leads": [...]
}
```

#### `GET /api/leads`
Get all extracted leads.

```json
// Request (query params)
?chatbot_id=uuid&limit=100

// Response
{
  "leads": [
    {
      "id": "uuid",
      "conversation_id": "uuid",
      "chatbot_id": "uuid",
      "name": "John Doe",
      "email": "john@example.com",
      "phone": "+1234567890",
      "company": "Acme Inc",
      "notes": "...",
      "created_at": "2026-01-22T..."
    }
  ]
}
```

---

## 9. FORMS

Handle form submissions from chatbot widgets.

#### `POST /api/forms/submit` (PUBLIC)
Submit form data from widget.

```json
// Request
{
  "form_id": "uuid",
  "conversation_id": "uuid",
  "visitor_id": "visitor_123",
  "submitted_data": {
    "name": "John",
    "email": "john@example.com"
  }
}
```

---

## 10. NOTIFICATIONS

Email sending via Resend.

#### `POST /api/notifications/email`
Send an email.

```json
// Request
{
  "to_email": "user@example.com",
  "subject": "Subject",
  "html_content": "<h1>Hello</h1>",
  "from_name": "Voxtro"
}
```

#### `POST /api/notifications/contact`
Handle contact form submissions.

---

## 11. WEBHOOKS

Handle webhooks from external services.

#### `POST /api/webhooks/vapi`
Receive VAPI end-of-call webhooks. Saves call records, transcripts, and recordings.

---

## Database Tables (Supabase)

| Table | Purpose |
|-------|---------|
| `chatbots` | Chatbot configurations |
| `chatbot_actions` | Tools/functions for chatbots |
| `chatbot_forms` | Forms attached to chatbots |
| `chatbot_faqs` | FAQ entries for chatbots |
| `conversations` | Chat conversations |
| `messages` | Individual messages |
| `customers` | Customer profiles |
| `support_tickets` | Support tickets |
| `voice_connections` | VAPI API keys per user |
| `voice_assistants` | Synced voice assistants |
| `voice_assistant_calls` | Call logs |
| `elevenlabs_connections` | ElevenLabs API keys per user |
| `whatsapp_agents` | Synced WhatsApp agents |
| `openai_connections` | OpenAI API keys per user |
| `leads` | Extracted leads |
| `token_usage` | Token tracking for billing |
| `response_cache` | Cached responses |
| `form_submissions` | Form submission data |

---

## Environment Variables (Frontend)

The frontend should have:
```
VITE_SUPABASE_URL=https://nzqzmvsrsfynatxojuil.supabase.co
VITE_SUPABASE_ANON_KEY=eyJhbGci...
VITE_API_URL=https://voxtro-backend.onrender.com
```

---

## Key Integration Points

1. **Auth**: Use Supabase Auth, pass token to backend in `Authorization` header
2. **Widget Embed Code**: Generate as `https://voxtro-backend.onrender.com/api/widget/{chatbot_id}/script.js`
3. **API Keys**: Let users input OpenAI/VAPI/ElevenLabs keys, save via backend
4. **Customer Portal**: Detect `is_customer` in user metadata, show different UI
5. **Real-time**: Use Supabase Realtime for live message updates if needed

---

## 12. WIDGET MESSENGER PAGE (REQUIRED)

The backend widget script loads an **iframe** that points to your Vercel frontend. You MUST create this page:

### Required Route
```
/messenger/[chatbotId]
```

Example URL:
```
https://dev.voxtro.io/messenger/fff796d6-9883-40b2-9101-0a96012700b7
```

### What this page should do:

1. **Extract `chatbotId`** from the URL params
2. **Fetch config** from: `GET https://voxtro-backend.onrender.com/api/widget/{chatbot_id}/config`
3. **Render a chat interface** with:
   - The chatbot's name, colors, avatar from config
   - First message display
   - Input field for user messages
4. **Send messages** to: `POST https://voxtro-backend.onrender.com/api/widget/{chatbot_id}/message`
   ```json
   {
     "visitor_id": "unique_visitor_id",
     "message": "user message",
     "conversation_id": "uuid or null"
   }
   ```
5. **Store `visitor_id`** in localStorage to maintain session
6. **Store `conversation_id`** from first response to continue conversation

### Important Notes:
- This page will be loaded inside an **iframe** on customer websites
- No authentication required (public endpoint)
- The page should be **responsive** and work well in the iframe dimensions (400x600px default)
- Without this page, the widget embed will show a 404 error
