# Voxtro Backend - Complete Deployment Guide

This guide will walk you through deploying the Voxtro backend from scratch, even if you've never done this before.

## Table of Contents
1. [Prerequisites](#prerequisites)
2. [Clone Your Supabase Database](#clone-supabase-database)
3. [Setup Backend Repository on GitHub](#setup-github)
4. [Deploy to Render](#deploy-to-render)
5. [Update Frontend](#update-frontend)
6. [Testing](#testing)
7. [Migration Execution](#migration-execution)

---

## 1. Prerequisites

### Accounts You Need:
- ✅ GitHub account (you have this)
- ✅ Supabase account with existing project (you have this)
- ❌ Render account (create at https://render.com - free tier available)

### Tools to Install:
```bash
# Install GitHub CLI (if not installed)
# On macOS:
brew install gh

# On Windows:
winget install --id GitHub.cli

# Login to GitHub
gh auth login
```

---

## 2. Clone Your Supabase Database

###  Step 2.1: Create New Supabase Project

1. Go to https://supabase.com/dashboard
2. Click "New Project"
3. **Project Name**: `voxtro-production-v2`
4. **Database Password**: Create strong password (SAVE THIS!)
5. **Region**: Choose same region as your current project
6. Click "Create new project"
7. Wait 2-3 minutes for setup

### Step 2.2: Export Data from Current Project

```bash
# Install Supabase CLI
npm install -g supabase

# Login to Supabase
supabase login

# Link to your CURRENT project
cd /workspaces/voxtro_app_Updated
supabase link --project-ref YOUR_CURRENT_PROJECT_ID

# Export database
supabase db dump -f backup.sql

# This creates backup.sql with all your data
```

**Alternative Method (if above fails):**
1. Go to your current Supabase dashboard
2. Click "Database" → "Backups"
3. Click "Download backup"

### Step 2.3: Import to New Project

```bash
# Link to NEW project
supabase link --project-ref YOUR_NEW_PROJECT_ID

# Import data
supabase db push backup.sql
```

**Verify Data Migration:**
```bash
# Compare table counts
supabase db diff
```

### Step 2.4: Copy Environment Variables

From your OLD Supabase project dashboard, copy:
- ✅ Project URL
- ✅ `anon` public key
- ✅ `service_role` secret key (Settings → API)
- ✅ JWT Secret (Settings → API → JWT Settings)

Save these - you'll need them for Render!

### Step 2.5: Enable OAuth in New Project

1. Go to Authentication → Providers
2. Enable **Google** (copy credentials from old project)
3. Enable **GitHub** (copy credentials from old project)

---

## 3. Setup Backend Repository on GitHub

### Step 3.1: Create GitHub Repository

```bash
# Navigate to backend directory
cd /workspaces/voxtro-backend

# Initialize git
git init
git add .
git commit -m "Initial backend setup"

# Create GitHub repo (creates public repo - change to private if needed)
gh repo create voxtro-backend --public --source=. --remote=origin

# Push code
git push -u origin main
```

Your backend is now on GitHub at: `https://github.com/YOUR_USERNAME/voxtro-backend`

### Step 3.2: Setup Frontend Repository (Separate from Backend)

```bash
# Navigate to frontend directory
cd /workspaces/voxtro_app_Updated

# Remove supabase functions (they're migrated to backend)
rm -rf supabase/functions

# Initialize as new repo if not already
git init
git add .
git commit -m "Frontend separated from backend"

# Create frontend repo
gh repo create voxtro-frontend --public --source=. --remote=origin
git push -u origin main
```

---

## 4. Deploy to Render

### Step 4.1: Create Render Account

1. Go to https://render.com
2. Click "Get Started"
3. Sign up with GitHub (click "GitHub" button)
4. Authorize Render to access your repositories

### Step 4.2: Deploy Backend

1. **From Render Dashboard**, click "New +" → "Web Service"

2. **Connect Repository**:
   - Click "Connect GitHub"
   - Select `voxtro-backend` repository
   - Click "Connect"

3. **Configure Service**:
   ```
   Name: voxtro-backend
   Region: Oregon (US West) or closest to your users
   Branch: main
   Root Directory: (leave empty)
   Runtime: Python 3
   Build Command: pip install -r requirements.txt
   Start Command: uvicorn app.main:app --host 0.0.0.0 --port $PORT
   Instance Type: Free (for testing) or Starter ($7/mo for production)
   ```

4. **Add Environment Variables** (Click "Advanced" → "Add Environment Variable"):
   ```
   SUPABASE_URL=https://YOUR_NEW_PROJECT.supabase.co
   SUPABASE_SERVICE_ROLE_KEY=your-service-role-key-from-new-project
   SUPABASE_ANON_KEY=your-anon-key-from-new-project
   SUPABASE_JWT_SECRET=your-jwt-secret-from-new-project
   OPENAI_API_KEY=your-openai-api-key
   RESEND_API_KEY=your-resend-api-key
   ENVIRONMENT=production
   ```

5. Click **"Create Web Service"**

6. Wait 5-10 minutes for deployment

7. Once deployed, you'll see: **"Your service is live at https://voxtro-backend.onrender.com"**

8. **Test it**: Open `https://voxtro-backend.onrender.com/health`
   - Should return: `{"status": "healthy"}`

### Step 4.3: Setup Cron Jobs (Background Tasks)

**For Lead Extraction:**
1. Click "New +" → "Cron Job"
2. Connect `voxtro-backend` repository
3. Configure:
   ```
   Name: extract-leads-cron
   Schedule: 0 */6 * * * (every 6 hours)
   Build Command: pip install -r requirements.txt
   Start Command: python -m app.tasks.lead_extraction
   ```
4. Add same environment variables
5. Create

**For Weekly Summary:**
1. Click "New +" → "Cron Job"
2. Connect `voxtro-backend` repository
3. Configure:
   ```
   Name: weekly-summary-cron
   Schedule: 0 9 * * 1 (Monday 9am)
   Build Command: pip install -r requirements.txt
   Start Command: python -m app.tasks.weekly_summary
   ```
4. Add same environment variables
5. Create

---

## 5. Update Frontend

### Step 5.1: Install Dependencies

```bash
cd /workspaces/voxtro_app_Updated
npm install axios
```

### Step 5.2: Update Environment Variables

Edit `.env` (or `.env.local`):
```env
# Update these to NEW Supabase project
VITE_SUPABASE_URL=https://YOUR_NEW_PROJECT.supabase.co
VITE_SUPABASE_ANON_KEY=your-new-anon-key

# Add backend URL
VITE_API_BASE_URL=https://voxtro-backend.onrender.com
```

### Step 5.3: Test Locally

```bash
npm run dev
```

Open http://localhost:5173 and verify:
- ✅ Can login
- ✅ Dashboard loads
- ✅ No console errors

---

## 6. Testing

### Test Backend Endpoints

```bash
# Health check
curl https://voxtro-backend.onrender.com/health

# API docs
# Open in browser: https://voxtro-backend.onrender.com/docs
```

### Test Frontend Integration

1. Login to your app
2. Create a test chatbot
3. Send a test message
4. Check if it works!

**Check Logs**:
- Render Dashboard → voxtro-backend → Logs tab
- Look for errors

---

## 7. Migration Execution (Production Cutover)

### When You're Ready to Migrate Live Users:

**IMPORTANT**: Schedule this during low-traffic hours!

### Step 7.1: Enable Maintenance Mode

Create `src/pages/Maintenance.tsx`:
```tsx
export default function Maintenance() {
  return (
    <div className="min-h-screen flex items-center justify-center">
      <div className="text-center">
        <h1 className="text-4xl font-bold mb-4">We'll be right back!</h1>
        <p>Voxtro is currently undergoing maintenance.</p>
        <p>We'll be back online shortly.</p>
      </div>
    </div>
  );
}
```

Update `src/App.tsx` to show maintenance page.

### Step 7.2: Deploy Frontend with New Backend

```bash
# Commit changes
git add .
git commit -m "Connect to new backend"
git push

# Deploy to your hosting (Vercel/Netlify/etc)
# Your frontend will now call the FastAPI backend!
```

### Step 7.3: Verify Everything Works

Test checklist:
- [ ] User login (email, Google, GitHub)
- [ ] Customer login
- [ ] Create chatbot
- [ ] Send chat messages
- [ ] Embed widget on test website
- [ ] Voice assistants sync
- [ ] WhatsApp agents sync
- [ ] Support tickets
- [ ] Dashboard analytics
- [ ] Real-time updates

### Step 7.4: Disable Maintenance Mode

Remove maintenance page, redeploy.

### Step 7.5: Monitor

Watch for 24 hours:
- Render logs (errors?)
- Supabase dashboard (query performance?)
- User feedback (bugs?)

---

## Troubleshooting

### Common Issues:

**1. "Module not found" error on Render**
- Check `requirements.txt` has all dependencies
- Rebuild: Render Dashboard → Manual Deploy → Clear build cache & deploy

**2. "Authentication failed" errors**
- Verify JWT secret is correct
- Check Supabase project URL matches

**3. "CORS error" in browser**
- Verify `VITE_API_BASE_URL` in frontend `.env`
- Check CORS settings in `app/middleware/cors.py`

**4. Slow API responses**
- Upgrade Render instance from Free to Starter ($7/mo)
- Free tier sleeps after inactivity

**5. Database connection errors**
- Verify service role key is correct
- Check Supabase project is active

---

## Cost Breakdown

### Monthly Costs:
- **Supabase**: $25/mo (Pro plan) - you're already paying this
- **Render Web Service**: $7/mo (Starter) or $0 (Free tier for testing)
- **Render Cron Jobs**: $1/mo each × 2 = $2/mo
- **OpenAI API**: Usage-based (same as before)
- **Resend Email**: Usage-based (same as before)

**Total New Cost**: ~$9/mo (or $0 if using free tier for testing)

---

## Next Steps After Deployment

1. ✅ Monitor error rates
2. ✅ Setup error tracking (Sentry)
3. ✅ Configure custom domain for backend
4. ✅ Add rate limiting
5. ✅ Setup automated backups
6. ✅ Create staging environment

---

## Getting Help

If you encounter issues:
1. Check Render logs: Dashboard → Service → Logs
2. Check Supabase logs: Dashboard → Logs Explorer
3. Check browser console for frontend errors
4. Review this guide again - step-by-step

---

## Summary

You now have:
- ✅ Separate backend (FastAPI on Render)
- ✅ Separate frontend (React - deploy wherever)
- ✅ New Supabase project (cloned data)
- ✅ All features working exactly the same
- ✅ More secure architecture
- ✅ Easier to maintain and scale

**Your backend is at**: `https://voxtro-backend.onrender.com`
**API docs at**: `https://voxtro-backend.onrender.com/docs`
