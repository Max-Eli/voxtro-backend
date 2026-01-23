-- Add AI summary fields to conversations table for chatbot conversation analysis
-- Similar to voice_assistant_calls summary fields

-- Add summary fields to conversations table
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS summary TEXT;
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS key_points JSONB;
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS action_items JSONB;
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS sentiment TEXT;
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS sentiment_notes TEXT;
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS conversation_outcome TEXT;
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS topics_discussed JSONB;
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS lead_info JSONB;
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS ended_at TIMESTAMPTZ;

-- Create index for faster queries on sentiment and outcome
CREATE INDEX IF NOT EXISTS idx_conversations_sentiment ON conversations(sentiment);
CREATE INDEX IF NOT EXISTS idx_conversations_outcome ON conversations(conversation_outcome);
