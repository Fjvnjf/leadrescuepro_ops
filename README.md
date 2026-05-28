# LeadRescuePro Operations Dashboard

Dark cyberpunk command center for managing Bangladesh-based SDRs cold-calling US plumbing businesses for LeadRescuePro.

## Live URLs

- GitHub Pages frontend: https://fjvnjf.github.io/lrp-dashboard-frontend/
- Local backend: http://127.0.0.1:8650
- Dashboard tunnel API base used by Pages: https://lrp-dash.loca.lt/api

The frontend sends `abypass-tunnel-reminder: 1` on every API request so loca.lt warning pages are bypassed for API calls.

## Login

- Admin username: `admin`
- Admin PIN: `4791`
- New SDR default PIN: `1234`

## Run Locally

```bash
python3 -m pip install -r dashboard_backend/requirements.txt
python3 dashboard_backend/main.py
```

Open http://127.0.0.1:8650. The same unified `dashboard_backend/static/index.html` is served for `/`, `/admin`, and `/caller`.

## Dashboard

The unified dashboard has one login and role-based tabs:

- Admin: Overview, Call Log, Prospects, Callers, Weekly, Commissions, Scraper, Clients, Revenue, Social/System.
- Caller: Overview, Call Log, Prospects.

It includes Hermes status, social/system health, source-tagged prospects, Loom sent tracking, SDR hiring pipeline, client management, revenue metrics, scraper execution, and Voiply webhook intake.
