-- Create whatsapp_conversations table if not exists
CREATE TABLE IF NOT EXISTS whatsapp_conversations (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL REFERENCES whatsapp_agents(id) ON DELETE CASCADE,
    phone_number TEXT,
    status TEXT DEFAULT 'active',
    started_at TIMESTAMPTZ DEFAULT NOW(),
    ended_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Create whatsapp_messages table if not exists
CREATE TABLE IF NOT EXISTS whatsapp_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id TEXT NOT NULL REFERENCES whatsapp_conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Add AI summary fields to whatsapp_conversations table
ALTER TABLE whatsapp_conversations ADD COLUMN IF NOT EXISTS summary TEXT;
ALTER TABLE whatsapp_conversations ADD COLUMN IF NOT EXISTS key_points JSONB;
ALTER TABLE whatsapp_conversations ADD COLUMN IF NOT EXISTS action_items JSONB;
ALTER TABLE whatsapp_conversations ADD COLUMN IF NOT EXISTS sentiment TEXT;
ALTER TABLE whatsapp_conversations ADD COLUMN IF NOT EXISTS sentiment_notes TEXT;
ALTER TABLE whatsapp_conversations ADD COLUMN IF NOT EXISTS conversation_outcome TEXT;
ALTER TABLE whatsapp_conversations ADD COLUMN IF NOT EXISTS topics_discussed JSONB;
ALTER TABLE whatsapp_conversations ADD COLUMN IF NOT EXISTS lead_info JSONB;

-- Create indexes
CREATE INDEX IF NOT EXISTS idx_whatsapp_conversations_agent_id ON whatsapp_conversations(agent_id);
CREATE INDEX IF NOT EXISTS idx_whatsapp_conversations_phone ON whatsapp_conversations(phone_number);
CREATE INDEX IF NOT EXISTS idx_whatsapp_conversations_sentiment ON whatsapp_conversations(sentiment);
CREATE INDEX IF NOT EXISTS idx_whatsapp_messages_conversation_id ON whatsapp_messages(conversation_id);

-- Create customer_whatsapp_agent_assignments table if not exists
CREATE TABLE IF NOT EXISTS customer_whatsapp_agent_assignments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL REFERENCES whatsapp_agents(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(customer_id, agent_id)
);
