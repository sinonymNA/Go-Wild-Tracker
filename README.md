# GoWild Radar ✈

A Railway-deployable web dashboard for tracking Frontier Airlines GoWild Pass flight availability from a chosen home airport. Displays routes on a mobile-first interactive map with Discord alerts when availability changes.

## Features

- **Interactive map** — Leaflet.js dark map showing routes from your home airport with green glowing lines for available GoWild flights
- **Mobile-first dashboard** — stats, filterable route cards, sticky bottom navigation
- **Scheduled scanning** — APScheduler runs Playwright to check Frontier routes on a configurable interval
- **Discord alerts** — notifies when a route changes from unavailable → available
- **Mock mode** — full dashboard with fake data, no browser required (deploy and test immediately)
- **Route management** — add/remove routes, bulk import CSV, enable/disable per route
- **PostgreSQL + SQLite** — Railway PostgreSQL in production, SQLite locally

---

## Quick Start (Local)

```bash
git clone <repo>
cd Go-Wild-Tracker

# Install Python deps
pip install -r requirements.txt
playwright install chromium

# Configure
cp .env.example .env
# Edit .env: set ADMIN_PASSWORD, keep MOCK_MODE=true for now

# Run
uvicorn app.main:app --reload

# Visit
open http://localhost:8000
# Login with your ADMIN_PASSWORD
# Trigger a scan from the bottom nav
```

---

## Mock Mode

`MOCK_MODE=true` (default) generates realistic fake GoWild availability data:
- ~55% of route/date combinations show as available
- Realistic prices ($5.60–$16.80 taxes/fees)
- Mix of nonstop and 1-stop connections
- Map populates with markers and route lines

This lets you deploy and verify the full stack before tuning Playwright selectors.

---

## Railway Deployment

1. Push this repo to GitHub
2. Create a new Railway project → **Deploy from GitHub repo**
3. Add a **PostgreSQL** plugin — Railway auto-injects `DATABASE_URL`
4. Set environment variables in Railway dashboard:
   ```
   ADMIN_PASSWORD=your-secure-password
   SECRET_KEY=<generate with: python -c "import secrets; print(secrets.token_hex(32))">
   MOCK_MODE=true
   HOME_AIRPORT=ATL
   ```
5. Deploy — Railway uses the `Dockerfile` automatically
6. Visit your Railway URL → `/login`

The `railway.json` configures the healthcheck at `/health`.

---

## Adding Routes

### Single route (UI)
Go to **Routes** → fill in Origin + Destination → Add Route

### CSV import (UI)
Go to **Routes** → paste CSV in the text area → Load Routes

CSV format (with or without header):
```
origin,destination
ATL,DEN
ATL,LAS
ATL,MCO
```

### Seed routes
19 ATL-origin routes are auto-loaded on first startup from `data/seed_routes.csv`.

---

## Enabling Real Scraping

The real Frontier scraper is in `app/scraper.py`. After deploying with mock mode:

1. Set `DEBUG_SCREENSHOTS=true` and `HEADLESS=false` locally
2. Set `MOCK_MODE=false`
3. Trigger a scan
4. Check `debug/` for screenshots and saved HTML
5. Open the HTML in a browser, inspect the DOM, find the real CSS selectors
6. Update the `SELECTOR_*` constants at the top of `scraper.py`
7. Re-test until results parse correctly

**Important:** The scraper never solves captchas or bypasses security. If Frontier blocks the browser, the scan returns `BLOCKED_CAPTCHA` status and moves on.

---

## Airport Data

Coordinates for 40+ airports are in `app/airport_data.py`. To add more:

```python
AIRPORTS["BOS"] = {"lat": 42.3656, "lon": -71.0096, "city": "Boston", "name": "Logan International"}
```

Routes with unknown airport coordinates still save to the database but are skipped on the map (a warning is logged).

---

## Project Structure

```
app/
  main.py           FastAPI app, all routes, startup lifecycle
  config.py         Pydantic settings (env vars)
  database.py       SQLAlchemy engine — PostgreSQL/SQLite auto-detect
  models.py         Route, ScanResult, AppSettings ORM models
  scraper.py        Playwright scraper (mock + real skeleton with TODOs)
  scheduler.py      APScheduler scan job
  alerts.py         Discord webhook alerts
  auth.py           Session cookie auth (itsdangerous)
  routes_manager.py Route CRUD + CSV import
  airport_data.py   IATA code → lat/lon dictionary
  templates/        Jinja2 HTML templates
  static/           app.css (glow effects), map.js (Leaflet)
data/
  seed_routes.csv   19 ATL starter routes
debug/              Debug screenshots + HTML (git-ignored)
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ADMIN_PASSWORD` | — | **Required.** Dashboard login password |
| `SECRET_KEY` | `change-me` | Session signing key — must be stable |
| `DATABASE_URL` | `sqlite:///./gowild.db` | SQLite local, PostgreSQL on Railway |
| `MOCK_MODE` | `false` | `true` = fake data, `false` = real Playwright |
| `HOME_AIRPORT` | `ATL` | Hub airport shown on map |
| `SCAN_INTERVAL_MINUTES` | `60` | How often the scheduler runs |
| `MAX_ROUTES_PER_SCAN` | `10` | Routes processed per scan cycle |
| `DELAY_BETWEEN_SEARCHES_SECONDS` | `5` | Polite delay between Frontier requests |
| `SCAN_DATES_AHEAD` | `14` | How many days forward to check |
| `HEADLESS` | `true` | Run Chromium headless (always true on Railway) |
| `DEBUG_SCREENSHOTS` | `false` | Save screenshots + HTML on scan failures |
| `DISCORD_WEBHOOK_URL` | — | Discord webhook for availability alerts |
| `PORT` | `8000` | HTTP port (Railway injects this) |
