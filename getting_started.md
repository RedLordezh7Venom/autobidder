# 🤖 AutoBid Bot — Getting Started

## Prerequisites
- Docker Desktop (or Docker Engine + Compose on Linux)
- A Groq API key (free at console.groq.com)
- LinkedIn account credentials
- X (Twitter) account credentials

## ⚡ Quick Start (5 minutes)

### 1. Clone & configure
```bash
cd bidding_bot
cp .env.example .env
```
Open `.env` and fill in:
- `GROQ_API_KEY` — your Groq key
- `LINKEDIN_EMAIL` / `LINKEDIN_PASSWORD`
- `X_USERNAME` / `X_PASSWORD` / `X_EMAIL`
- `WORKER_SECRET` — any random string (used to secure the API)

### 2. Start the stack
```bash
docker compose up -d
```
First run downloads ~2GB (Playwright Chromium + Python). Subsequent starts are instant.

### 3. Open the Dashboard
👉 **http://localhost:3000**

You'll see live stats, keyword management, and config.

### 4. Import the n8n Workflow
1. Open n8n at **http://localhost:5678**
2. Create an account (first run only)
3. Go to **Workflows → Import** → upload `n8n/workflows/main_workflow.json`
4. Add credential: `HTTP Header Auth` → Header `Authorization`, Value `Bearer <your WORKER_SECRET>`
5. **Activate** the workflow

The bot will now run automatically during business hours (9 AM–6 PM EST, Mon–Fri).

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  n8n (port 5678)           Dashboard (port 3000)        │
│  Hourly schedule ──────►  Monitor stats, manage config  │
│       │                                                 │
│       ▼                                                 │
│  Worker API (port 8080)                                 │
│  POST /scrape ──► Playwright scrapes LinkedIn & X       │
│  POST /post   ──► AI pipeline → Playwright posts bid    │
│       │                                                 │
│       ▼                                                 │
│  PostgreSQL ──► Dedup + audit log                       │
│  Redis      ──► Rate limit state                        │
└─────────────────────────────────────────────────────────┘
```

## Services

| Service   | URL                    | Purpose                        |
|-----------|------------------------|--------------------------------|
| Dashboard | http://localhost:3000  | Monitoring & control UI        |
| n8n       | http://localhost:5678  | Workflow orchestration         |
| Worker    | http://localhost:8080  | Playwright automation API      |
| Postgres  | localhost:5432         | Database                       |

## Customization

### Keywords
Edit search keywords in the Dashboard → Keywords tab. Changes take effect on the next run.

### Daily limits / delays
Edit in Dashboard → Config tab. Key settings:
- `linkedin_daily_limit` — default 12
- `x_daily_limit` — default 7
- `min_delay_minutes` / `max_delay_minutes` — human-like gap between posts

### Bid prompt
Edit `worker/prompts.yaml` and restart the worker:
```bash
docker compose restart worker
```

### Skills (for AI context)
Managed in the database. Connect via:
```bash
docker compose exec postgres psql -U bidbot -d bidding_bot
INSERT INTO skills_profile (category, skill) VALUES ('frameworks', 'Next.js');
```

### Proxy (recommended for production)
Set `PROXY_URL=http://user:pass@host:port` in `.env`.

## Alerts
Set `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` in `.env` to receive alerts for:
- Circuit breaker trips
- Daily limit reached
- Each successful bid posted

## Logs
```bash
docker compose logs -f worker    # Playwright activity
docker compose logs -f n8n       # Workflow runs
```

## Stopping
```bash
docker compose down              # Stop, keep data
docker compose down -v           # Stop and wipe database
```
