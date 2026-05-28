# LEADRESCUEPRO COLD CALLING DASHBOARD - COMPLETE BUILD PLAN
## Instructions for Local Codex CLI

---

## WHO YOU ARE BUILDING FOR
- **Owner:** Fahim (fahim@leadrescuepro.com)
- **GitHub:** Fjvnjf (personal) + leadrescuepro (org)
- **Business:** LeadRescuePro LLC — AI receptionist + human SDR cold calling service for US plumbing companies
- **Pricing:** $997 setup, $499/month, $0.50/min, month-to-month
- **SDRs:** Bangladesh-based, pure commission $100/closed deal, no salary, night shift (US business hours = BD night), 100 quality dials/day
- **SDR workflow:** Cold call plumbing businesses → get permission to send a 3-minute Loom video (appointment setting, NOT selling on call)

---

## WHAT EXISTS RIGHT NOW (Server State)

### Files on disk at /home/hermeseassistant/leadrescuepro_ops/dashboard_backend/

**main.py** (826 lines) - FastAPI backend with all routes:
- Auth: POST /api/auth/login (form + JSON body), GET /api/auth/me
- Prospects: GET/POST /api/prospects, PATCH/DELETE /{id}, POST /import, POST /{id}/assign
- Calls: POST /api/calls/log, GET /api/calls, GET /today, GET /export, POST /{id}/recording, POST /{id}/transcribe
- KPIs: GET /api/kpis/dashboard, /weekly, /daily-summary, /caller/{id}
- Commissions: GET/POST /api/commissions, GET /summary, PATCH /{id}
- Callers: GET/POST /api/callers, GET /{id}/performance
- Recordings: POST /api/recordings/upload, GET /{id}/play, POST /{id}/transcribe
- Leads: POST /api/leads/scrape (placeholder)
- Health: GET /api/health
- Frontend: GET / (admin.html), GET /caller (caller.html), GET /admin

**database.py** (126 lines) - SQLAlchemy models:
- User (id, username, pin_hash, role, full_name, active, created_at)
- Prospect (id, business_name, phone, city, state, rating, reviews, website, score, status, assigned_to, last_contact, notes, created_at)
- CallLog (id, prospect_id, caller_id, business_name, phone, disposition, notes, duration_seconds, recording_path, transcript, call_time, created_at)
- Commission (id, caller_id, prospect_id, deal_amount, commission_amount, status, deal_date, paid_date, notes)
- Recording (id, call_id, caller_id, file_path, duration_seconds, file_size, transcribed, transcript, created_at)

**auth.py** (73 lines) - JWT auth with PIN-based login:
- PIN = 4 digits, SHA256 hash with salt
- JWT tokens expire after 48 hours
- Two roles: admin, caller
- Default admin user: username=admin, PIN=4791

**static/admin.html** (28KB) - Admin command center:
- 6 tabs: Overview, Prospects, Callers, Commissions, Weekly, Log Call
- Dark cyberpunk theme (BMW cluster + Jarvis)
- Login with username + 4-digit PIN
- Auto-refresh every 30 seconds
- Daily summary panel, quick log, export CSV
- API base: /api (served from same origin when via FastAPI)
- On GitHub Pages: API base = https://lrp-dash.loca.lt/api

**static/caller.html** (19KB) - SDR workspace:
- Login, quick call log form, today's stats, all-time performance, call log table, activity feed
- Prospect dropdown auto-fill
- Auto-refresh every 30 seconds
- Dispositions: voicemail, no_answer, talked_interested, talked_not_interested, callback_set, dnc

### Database State
- SQLite at data/leadrescuepro.db
- 101 prospects (95 from CSV leads: Austin, San Antonio, Houston, Dallas)
- 10 test calls
- 0 commissions
- 2 users: admin (Fahim) + rahim (sample SDR)

### Running Services
- Backend server: FastAPI on port 8650 (uvicorn)
- Tunnel: loca.lt at https://lrp-dash.loca.lt
- Guardian: watchdog script auto-restarts server + tunnel
- Cron: health check every 5 minutes (job_id: 09a03dc82591)
- GitHub Pages: https://fjvnjf.github.io/lrp-dashboard-frontend/ (gh-pages branch)

### GitHub Repos
- https://github.com/Fjvnjf/leadrescuepro_ops (main project, has dashboard_backend/ subdir)
- https://github.com/Fjvnjf/lrp-dashboard-backend (private, backend code only)
- https://github.com/Fjvnjf/lrp-dashboard-frontend (public, GitHub Pages deployment)
- https://github.com/LeadRescuePro/leadrescuepro (doesn't exist yet - 404)

### GitHub Tokens
- Tokens stored in ~/.hermes/.env (GITHUB_TOKEN and GITHUB_TOKEN_SECONDARY)
- Also in ~/.git-credentials for repo push operations
- GITHUB_CREDS.md at root of repo has the actual tokens — read that file for pushing

### Other LeadRescuePro Assets
- SOP manual: ~/leadrescuepro_ops/SOP-TRAINING-MANUAL-Outbound-SDR.md
- Lead scraper: ~/leadrescuepro_ops/prospect-scraper.js
- Lead CSVs in ~/leadrescuepro_ops/leads/ (4 files)
- 8 dashboard HTML files in ~/leadrescuepro_ops/dashboards/
- Voice bridge at ~/leadrescuepro_ops/voice_bridge.py
- Atlas Social MCP connected: Instagram @leadrescuepro, LinkedIn (Fahim's profile)
- Postiz tool installed (needs Fahim to authenticate)
- Tunnel guardian at ~/leadrescuepro_ops/guardian.py

### Server Environment
- OS: WSL (Windows Subsystem for Linux)
- Timezone: Asia/Dhaka (+6)
- Python 3.11
- All timestamps stored as UTC naive datetimes (datetime.utcnow())
- KPI filtering uses UTC day boundaries (datetime.now(timezone.utc))
- Port 8650 for backend, loca.lt tunnel subdomain: lrp-dash
- CORS: allow_origins=["*"]

---

## WHATS BROKEN / NEEDS FIXING

### 1. Tunnel Warning Page
localtunnel shows a warning page on first browser visit per IP per 7 days.
- Browser requests (User-Agent: Mozilla/...) get intercepted by tunnel warning
- API requests (no browser UA or with "abypass-tunnel-reminder: 1" header) pass through fine
- GitHub Pages frontend works but browser's same-origin policy prevents it from calling the tunnel API unless CORS is configured properly
- **SOLUTION NEEDED:** Either switch to cloudflared tunnel (no warning), or add CORS middleware that allows GitHub Pages origin, OR self-host with a proper domain

### 2. CORS Issue
The GitHub Pages frontend at https://fjvnjf.github.io calls the tunnel API at https://lrp-dash.loca.lt.
- Current CORS allows "*" so this should work
- BUT the tunnel itself might block cross-origin requests
- **FIX:** Test thoroughly and either fix CORS on the FastAPI side or use a different API proxy strategy

### 3. Frontend Needs Complete Rewrite
Current frontend is two separate files (admin.html, caller.html). Need a SINGLE unified dashboard that:
- Has proper responsive design for mobile
- Uses the tunnel API with bypass headers
- Looks like the BMW cyberpunk/Jarvis instrument cluster design
- All 8 separate dashboards should be accessible from one place

### 4. Missing Business Features
- No client onboarding workflow (track new plumbing customers through setup)
- No revenue tracking for LeadRescuePro itself (MRR, churn, clients)
- No content calendar integration with Atlas Social
- No lead scraping UI (the scraper script exists but no frontend for it)
- No daily report auto-generation
- No SDR hiring pipeline UI

### 5. Missing Backend Endpoints
- No actual lead scraper (endpoint is a placeholder)
- No client management
- No subscription/billing tracking
- No daily report PDF generation
- No webhook for Voiply call data integration

---

## COMPLETE SPECIFICATION FOR NEW DASHBOARD

### Overall Architecture
```
┌─────────────────────┐     ┌──────────────────┐     ┌──────────────┐
│  GitHub Pages       │────▶│  loca.lt Tunnel  │────▶│  FastAPI     │
│  (Static HTML/JS)   │     │  (API proxy)     │     │  Backend     │
│  fjvnjf.github.io/  │     │  lrp-dash.loca.lt│     │  Port 8650   │
└─────────────────────┘     └──────────────────┘     └──────┬───────┘
                                                            │
                                                   ┌───────┴───────┐
                                                   │   SQLite DB   │
                                                   │ leadrescuepro │
                                                   └───────────────┘
```

### Frontend: Single Unified Dashboard (index.html)

**DESIGN SYSTEM:**
- Dark cyberpunk / BMW instrument cluster
- Colors: --bg:#02040a, --panel:#07101b, --cyan:#31d7ff, --green:#2dff9a, --amber:#ffd166, --red:#ff416d, --lime:#b7ff36, --muted:#7d9bb7, --line:#173653
- Font: DM Sans (Google Fonts)
- Grid background pattern (cyberpunk scanlines)
- Animated glow effects on active elements
- Mobile-first responsive: single column on phone, multi-column on desktop
- Bottom tab bar for mobile, sidebar for desktop

**LOGIN PAGE:**
- Username + 4-digit PIN
- Stores token + user in localStorage
- Auto-login on page load if token exists
- Shows error messages on invalid login
- Two user roles: admin sees everything, caller sees limited view

**TABS (bottom nav on mobile, sidebar on desktop):**

1. **OVERVIEW** - Command center dashboard
   - 4 KPI cards: Dials Today, Connects, Interested, DNC Rate
   - Daily summary text panel
   - Recent activity feed (last 20 calls)
   - Top callers ranking
   - Disposition breakdown

2. **CALL LOG** - Log a call (for both admin and callers)
   - Business name (with autocomplete from prospects DB)
   - Phone
   - Disposition dropdown: Voicemail, No Answer, Talked-Interested, Talked-Not Interested, Callback Set, DNC
   - Duration (seconds)
   - Notes textarea
   - Submit button - auto-creates prospect if new
   - Shows confirmation with call ID after logging

3. **PROSPECTS** - Lead database
   - Search bar + status filter dropdown
   - Table: Name, Phone, City, Score (A/B/C color-coded), Status, Last Contact
   - Pagination or scroll
   - "Import CSV" button (file upload)
   - "Assign to Caller" per prospect

4. **CALLERS** - SDR management
   - List: Name, Username, Status (Active/Blocked)
   - Add caller form: Username, Full Name, PIN
   - Per-caller stats: today's dials, connects, rate (from /api/kpis/caller/{id})
   - All-time performance (from /api/callers/{id}/performance)

5. **WEEKLY** - 7-day performance view
   - Bar chart for each day of the week
   - Dials, connects, DNC per day
   - Color-coded bars (green=good, amber=ok, red=bad)

6. **COMMISSIONS** - Commission tracking
   - Summary by caller: name, total deals, total commission, paid count
   - Grand total
   - All commissions table: caller, deal amount, commission, status, date
   - "Add Commission" form (admin only)
   - "Mark as Paid" button per commission

7. **SCRAPER** - Lead scraping tool
   - City + State input fields
   - "Scrape Leads" button (calls POST /api/leads/scrape)
   - Shows last scrape status
   - Upload CSV to import

8. **CLIENTS** - Client management (NEW - needs backend)
   - List of active clients
   - Onboarding progress
   - Subscription status
   - Monthly revenue per client

9. **REVENUE** - Business metrics (NEW - needs backend)
   - MRR, total clients, churn rate, ARPU
   - Revenue trend
   - Commission payouts total

10. **SOCIAL** - Content calendar view (visual reference)
    - Shows content schedule for the week
    - Integrated with Atlas Social (but just a view, posting via separate system)

**CRITICAL IMPLEMENTATION DETAILS:**
- All API calls must include the "abypass-tunnel-reminder: 1" header to bypass loca.lt warning
- API BASE URL should be configurable: default "/api" when served from FastAPI, fallback to "https://lrp-dash.loca.lt/api" when on GitHub Pages
- Detect environment: check if window.location.hostname includes "github.io" → use tunnel URL
- All fetch calls: `fetch(API_BASE + path, { headers: { "abypass-tunnel-reminder": "1", "Authorization": "Bearer " + token } })`
- Use localStorage for token persistence (key: "lrp_admin_token" / "lrp_caller_token")
- Auto-refresh overview every 30 seconds
- Mobile responsive: viewport meta tag, @media queries for 860px and 520px breakpoints
- Touch-friendly: all buttons minimum 44px height
- No external dependencies except Google Fonts (DM Sans)

### Backend: Fixes and Additions

**FIXES NEEDED:**
1. Add "abypass-tunnel-reminder" header to all responses via middleware
2. Ensure CORS allows the GitHub Pages origin
3. Fix any remaining timezone issues in KPI queries

**NEW ENDPOINTS NEEDED:**
1. `GET /api/clients` - List active clients
2. `POST /api/clients` - Add new client
3. `GET /api/revenue` - Revenue metrics (MRR, total, churn)
4. `POST /api/scraper/run` - Actually run the scraper (call prospect-scraper.js)
5. `GET /api/reports/daily` - Generate daily report text

### GitHub Pages Deployment

The frontend is already deployed. To update:
```bash
# Prepare the files
# Set API_BASE to "https://lrp-dash.loca.lt/api" in the HTML
# Add bypass header to all fetch calls
COPY index.html AND caller.html TO a clean directory
git init && git checkout -b gh-pages
git add -A && git commit -m "Dashboard update"
git remote add origin https://github.com/Fjvnjf/lrp-dashboard-frontend.git
git push -u origin gh-pages --force
```

Then GitHub Actions will auto-build. Site goes live at:
https://fjvnjf.github.io/lrp-dashboard-frontend/

---

## END GOAL

When Fahim opens his phone browser and goes to:
```
https://fjvnjf.github.io/lrp-dashboard-frontend/
```

He should see a **beautiful, fully functional dark cyberpunk command center** that:

1. **Logs in instantly** (token persists in localStorage)
2. **Shows live KPIs** from today's calling activity
3. **Lets him log calls** on behalf of SDRs
4. **Shows prospect database** with search/filter
5. **Manages callers** (add new SDRs, see their performance)
6. **Tracks commissions** (who earned what, what's paid/pending)
7. **Shows weekly performance** in a visual chart
8. **Provides a scraper tool** to generate new leads
9. **Works perfectly on mobile** (responsive, touch-friendly)
10. **Auto-recovers** if the backend or tunnel goes down

The API tunnel at lrp-dash.loca.lt should work seamlessly in the background without showing any warning pages (using the bypass header).

---

## IMMEDIATE NEXT STEPS (What Codex Should Build)

### Phase 1: Fix the Tunnel Issue (MUST DO FIRST)
1. Add middleware to FastAPI to set "abypass-tunnel-reminder: 1" on all responses
2. Test that the CORS allows GitHub Pages origin
3. Update the frontend to auto-detect if it's on GitHub Pages vs same-origin

### Phase 2: Build the Unified Dashboard (index.html)
Create ONE single HTML file that replaces both admin.html and caller.html:
- Detection logic: if user is admin, show all tabs; if caller, show limited tabs
- Same cyberpunk dark theme
- All 10 tabs listed above
- Mobile-first responsive

### Phase 3: Add Missing Backend Endpoints
- Client management (CRUD)
- Revenue tracking
- Daily report generation

### Phase 4: Deploy Everything
- Push updated frontend to GitHub Pages
- Restart the backend server
- Verify everything works from mobile phone
