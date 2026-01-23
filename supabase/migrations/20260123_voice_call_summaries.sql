-- Add AI summary columns to voice_assistant_calls table
-- These store AI-generated analysis of call transcripts

ALTER TABLE voice_assistant_calls
ADD COLUMN IF NOT EXISTS summary TEXT,
ADD COLUMN IF NOT EXISTS key_points JSONB DEFAULT '[]'::jsonb,
ADD COLUMN IF NOT EXISTS action_items JSONB DEFAULT '[]'::jsonb,
ADD COLUMN IF NOT EXISTS sentiment TEXT,
ADD COLUMN IF NOT EXISTS sentiment_notes TEXT,
ADD COLUMN IF NOT EXISTS call_outcome TEXT,
ADD COLUMN IF NOT EXISTS topics_discussed JSONB DEFAULT '[]'::jsonb,
ADD COLUMN IF NOT EXISTS lead_info JSONB;

-- Create indexes for common queries
CREATE INDEX IF NOT EXISTS idx_voice_calls_sentiment ON voice_assistant_calls(sentiment);
CREATE INDEX IF NOT EXISTS idx_voice_calls_outcome ON voice_assistant_calls(call_outcome);
