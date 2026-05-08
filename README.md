# 🤖 AutoBid — Autonomous AI Bidding Agent

An enterprise-grade, fully autonomous AI-driven system that discovers freelance/contract opportunities on **LinkedIn** and **X (Twitter)**, qualifies them with an LLM pipeline, and fires hyper-personalized bids — with **zero human intervention**.

---

## 📦 Stack at a Glance

| Layer | Technology |
|---|---|
| **Orchestration** | n8n (self-hosted, schedule + webhook driven) |
| **Worker API** | FastAPI + Playwright (Python 3.11, Bookworm) |
| **Browser Automation** | Playwright + Xvfb (headless Linux display) |
| **AI / LLM** | Groq (primary, ultra-low latency) → OpenRouter (fallback) |
| **Database** | PostgreSQL 16 (dedup, audit log, state) |
| **Queue / Rate State** | Redis 7 (daily counters, session caching) |
| **Alerting** | Telegram Bot |
| **Monitoring UI** | Custom Dashboard (port 3000) |

---

## 🏗️ Architecture

```
┌──────────────────────────────────────────────────────────┐
│                        n8n (5678)                         │
│   Schedule → Scrape → Qualify → Draft → Execute → Audit  │
└────────────────────────┬─────────────────────────────────┘
                         │  HTTP (Bearer Token)
                         ▼
┌──────────────────────────────────────────────────────────┐
│              FastAPI Worker API (8080)                     │
│                                                           │
│  POST /scrape   →  LinkedIn/X Playwright agents           │
│  POST /post     →  Intent → Pain Point → Bid → Post       │
│  POST /reset    →  Manual circuit breaker reset           │
│  GET  /stats    →  Live metrics for Dashboard             │
│  GET  /health   →  Health probe                           │
└───────────┬────────────────────────┬──────────────────────┘
            │                        │
            ▼                        ▼
   ┌─────────────────┐    ┌─────────────────────┐
   │  PostgreSQL (5432)│    │    Redis (6379)      │
   │  - interactions  │    │  - daily counters    │
   │  - circuit state │    │  - rate limit state  │
   │  - config table  │    └─────────────────────┘
   │  - keywords      │
   └─────────────────┘
            │
            ▼
   ┌─────────────────────┐
   │  Dashboard (3000)   │
   │  Reads stats + DB   │
   └─────────────────────┘
```

---

## ⚙️ How the Constraint System Works

### 1. Business Hours Enforcement
Every `/scrape` and `/post` call first checks if the current server time falls within the configured operating window (default `09:00–18:00 EST`). This is stored in the `config` DB table so it can be changed at runtime without redeploying.

```
Outside hours → return { skipped_reason: "outside_business_hours" }
```

### 2. Deduplication (Idempotency)
Before any action is taken on a post, its `post_id` is checked against the `interactions` table. If a matching `(platform, post_id)` pair already exists, the post is silently skipped. This prevents double-bidding even if n8n triggers the same run twice.

```
DB check → already posted? → skip immediately, return reason
```

### 3. Daily Rate Limits
Per-account counters are stored in Redis and are atomic. Defaults are `12 comments/day` on LinkedIn and `7 replies/day` on X. These thresholds are configurable via the `config` DB table and enforced before any Playwright session is even opened.

```
Redis counter ≥ limit → return { reason: "daily_limit_reached" }
```

### 4. Circuit Breaker
Three consecutive Playwright failures (proxy down, CAPTCHA, DOM change, etc.) on the same platform trip the circuit breaker. The state is persisted in PostgreSQL's `circuit_breaker_state` table. While tripped:
- All scrape and post calls for that platform are rejected instantly
- A Telegram alert fires
- The circuit can only be reset manually via `POST /reset` or automatically on the next successful run

```
failure_count ≥ threshold → trip circuit → alert → halt platform
success → reset failure_count → reopen circuit
```

### 5. AI Intent Classification (Pre-filter)
Before spending tokens on bid generation, every post passes through a **binary classifier** (`classify_intent`). Posts that are not genuine freelance/contract opportunities (ads, reposts, engagement bait, crypto spam) are logged as `skipped` and never acted upon. This protects accounts and conserves API quota.

```
classify_intent() → False → record "skipped" → return early
```

### 6. LLM Fallback Chain
Groq is the primary provider (ultra-low latency, Llama-3 / Mixtral). If Groq rate-limits or errors, `ai_client.py` automatically retries via OpenRouter (same OpenAI-compatible interface). This is handled transparently — n8n never sees a failure unless both providers fail.

```
Groq → rate limit / error → OpenRouter → error → raise exception → circuit records failure
```

---

## 🔒 Stealth & Anti-Detection

| Technique | Implementation |
|---|---|
| **Browser fingerprinting** | Randomized Canvas, WebGL, Audio, Font per session |
| **Human mouse movement** | Bezier curve algorithms — no straight-line movements |
| **Typing emulation** | Variable keystroke delay (50–150ms), simulated typos + backspace |
| **Scroll behavior** | Variable speed with random read-pauses |
| **Session isolation** | Persistent encrypted cookies per account in `/app/sessions` |
| **Proxy routing** | Residential sticky-session proxy per account, IP matches account region |
| **Temporal jitter** | Random 12–47 min wait between actions, only within business hours |
| **Headless display** | Xvfb virtual display — full browser UI with no physical screen |

---

## 📈 Scalability

The architecture is horizontally scalable at every layer:

| Bottleneck | Scale Strategy |
|---|---|
| **More accounts** | Add credentials to `.env`; each account gets its own rate-limit key in Redis and an isolated browser session. No code changes required. |
| **More platforms** | Add a new `Agent` class (e.g., `upwork_agent.py`), register it in `main.py`, add keywords to the DB. |
| **Higher volume** | Run multiple `worker` container replicas behind a load balancer. Redis counters are atomic across instances. |
| **DB bottleneck** | PostgreSQL with `asyncpg` + SQLAlchemy async — handles hundreds of concurrent connections. Add read replicas for the dashboard if needed. |
| **LLM throughput** | Groq's free tier supports ~30 req/min. Increase by adding more OpenRouter fallback models or upgrading plan. The fallback chain is already wired. |
| **n8n throughput** | Increase concurrent executions in n8n settings, or run a separate n8n instance per platform. |

---

## 🚀 Quick Start

### Prerequisites
- Docker Desktop (or Docker + Compose on Linux)
- A Groq API key ([console.groq.com](https://console.groq.com))
- LinkedIn and/or X credentials
- (Optional) Residential proxy URL

### 1. Configure environment
```bash
cp .env.example .env
# Edit .env and fill in your keys and credentials
```

### 2. Build and start
```bash
docker compose up --build -d
```

### 3. Access the services

| Service | URL |
|---|---|
| **n8n Workflow Editor** | http://localhost:5678 |
| **Worker API Docs** | http://localhost:8080/docs |
| **Dashboard** | http://localhost:3000 |

### 4. Import n8n workflows
In the n8n UI, go to **Workflows → Import** and load the JSON files from `./n8n/workflows/`.

---

## 🗂️ Project Structure

```
bidding_bot/
├── docker-compose.yml       # Full stack definition
├── .env.example             # All required env vars documented
├── db/
│   └── init.sql             # PostgreSQL schema (auto-applied on first boot)
├── worker/
│   ├── Dockerfile           # python:3.11-slim-bookworm + Playwright
│   ├── requirements.txt
│   ├── main.py              # FastAPI app — all endpoints
│   ├── config.py            # Pydantic settings (reads from env)
│   ├── database.py          # Async SQLAlchemy DB helpers
│   ├── ai_client.py         # Groq → OpenRouter LLM pipeline
│   ├── linkedin_agent.py    # LinkedIn Playwright automation
│   ├── x_agent.py           # X (Twitter) Playwright automation
│   ├── stealth_browser.py   # Browser init with anti-detection
│   ├── alerting.py          # Telegram alert dispatchers
│   ├── prompts.yaml         # Versioned LLM system prompts
│   └── entrypoint.sh        # Starts Xvfb then Uvicorn
├── n8n/
│   └── workflows/           # n8n workflow JSON exports
├── dashboard/
│   └── Dockerfile           # Monitoring UI
└── specs.md                 # Original engineering spec
```

---

## 🔑 Environment Variables

| Variable | Required | Description |
|---|---|---|
| `POSTGRES_PASSWORD` | ✅ | PostgreSQL password |
| `REDIS_PASSWORD` | ✅ | Redis auth password |
| `GROQ_API_KEY` | ✅ | Primary LLM provider |
| `OPENROUTER_API_KEY` | ✅ | LLM fallback provider |
| `WORKER_SECRET` | ✅ | Bearer token for Worker API auth |
| `N8N_ENCRYPTION_KEY` | ✅ | 32-char n8n credential encryption key |
| `LINKEDIN_EMAIL` | ✅ | LinkedIn account email |
| `LINKEDIN_PASSWORD` | ✅ | LinkedIn account password |
| `X_USERNAME` | ✅ | X (Twitter) handle |
| `X_PASSWORD` | ✅ | X account password |
| `X_EMAIL` | ✅ | X account email (for login) |
| `PROXY_URL` | ⚠️ Recommended | `http://user:pass@host:port` |
| `TELEGRAM_BOT_TOKEN` | Optional | For circuit breaker alerts |
| `TELEGRAM_CHAT_ID` | Optional | Telegram chat to alert |
| `LOG_LEVEL` | Optional | `DEBUG` / `INFO` (default: `INFO`) |

---

## ⚠️ Non-Negotiable Safety Rules

1. **No official posting APIs** — all actions are 100% UI automation to mimic real users.
2. **Fail gracefully** — unexpected DOM elements or CAPTCHAs cause a skip + log, never a crash.
3. **Circuit breaker is sacred** — 3 failures halts the platform. Manual review required before reset.
4. **Business hours only** — never run at night. Accounts that post at 3AM get flagged instantly.
5. **Proxy is mandatory for production** — do not run against real accounts without a residential proxy.
