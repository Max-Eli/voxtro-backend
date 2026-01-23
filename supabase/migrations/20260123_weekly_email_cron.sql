-- Enable the pg_cron and pg_net extensions if not already enabled
CREATE EXTENSION IF NOT EXISTS pg_cron;
CREATE EXTENSION IF NOT EXISTS pg_net;

-- Grant usage to postgres role
GRANT USAGE ON SCHEMA cron TO postgres;

-- Schedule the weekly email cron job
-- Runs every Monday at 9:00 AM UTC
SELECT cron.schedule(
    'weekly-emails-job',
    '0 9 * * 1',
    $$
    SELECT net.http_post(
        url := 'https://nzqzmvsrsfynatxojuil.supabase.co/functions/v1/weekly-emails',
        headers := '{"Content-Type": "application/json"}'::jsonb,
        body := '{}'::jsonb
    ) AS request_id;
    $$
);
