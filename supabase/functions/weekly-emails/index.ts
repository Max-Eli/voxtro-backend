// Supabase Edge Function for Weekly Email Updates
// Schedule: Every Monday at 9:00 AM UTC

import { serve } from "https://deno.land/std@0.168.0/http/server.ts"

const BACKEND_URL = Deno.env.get('BACKEND_URL') || 'https://voxtro-backend.onrender.com'
const CRON_SECRET = Deno.env.get('CRON_SECRET') || ''

serve(async (req) => {
  try {
    console.log('Starting weekly email cron job...')

    // Call the backend endpoint
    const response = await fetch(
      `${BACKEND_URL}/api/notifications/cron/weekly-emails?cron_secret=${CRON_SECRET}`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
      }
    )

    const result = await response.json()

    console.log('Weekly emails result:', result)

    return new Response(
      JSON.stringify({
        success: true,
        message: 'Weekly emails triggered',
        result: result
      }),
      {
        headers: { 'Content-Type': 'application/json' },
        status: 200
      }
    )

  } catch (error) {
    console.error('Weekly email cron error:', error)

    return new Response(
      JSON.stringify({
        success: false,
        error: error.message
      }),
      {
        headers: { 'Content-Type': 'application/json' },
        status: 500
      }
    )
  }
})
