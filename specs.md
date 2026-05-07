# 🚀 Enterprise-Grade Auto-Bidding & Lead Generation Agent (LinkedIn & X)

## 1. Executive Summary
Develop a State-of-the-Art (SOTA), fully autonomous AI-driven bidding system designed to identify high-intent freelance/contract opportunities on LinkedIn and X (Twitter) and engage with them using hyper-personalized, context-aware comments. This system must operate with **zero human intervention**, maintain **absolute stealth** to prevent account bans, and utilize **enterprise-grade architecture** for scalability, observability, and fault tolerance.

## 2. Core Architecture & Tech Stack
*   **Orchestration Engine:** n8n (Dockerized, Webhook/Schedule driven, self-hosted).
*   **Scraping & Execution (Workers):** Python 3.11+, Playwright with `playwright-stealth` and custom browser fingerprinting.
*   **Intelligence (LLM):** Groq (Primary for ultra-low latency Llama-3/Mixtral) -> OpenRouter (Fallback).
*   **Database & State Management:** PostgreSQL/SQLite (Replacing Google Sheets for transactional integrity, speed, and concurrency).
*   **Proxy & Network:** Rotational Residential Proxies with sticky sessions per social account.

## 3. Advanced Engineering Specifications

### 3.1. Stealth & Anti-Bot Evasion (CRITICAL)
Platform detection (especially LinkedIn) is highly aggressive. The Playwright worker must implement:
*   **Browser Fingerprinting:** Randomized but consistent Canvas, WebGL, Audio, and Font fingerprints per account session using extensions like `undetected-chromedriver` paradigms or Playwright stealth plugins.
*   **Human-like Emulation:**
    *   **Mouse Movements:** Bezier curve algorithms for natural, non-linear mouse tracking.
    *   **Scrolling:** Variable speed scrolling with random pauses to read content.
    *   **Typing:** Variable keystroke delays (e.g., 50-150ms), simulated typos, and backspace corrections.
*   **Session Management:** Encrypted persistent cookie storage. Sessions must be naturally aged, warmed up, and completely isolated.
*   **Network Obfuscation:** Traffic must route through high-quality residential proxies. The IP must match the geographic persona of the account.
*   **Temporal Randomization:** Actions must strictly occur within localized business hours (e.g., 9 AM - 6 PM EST), with pseudo-randomized jitter between every action (e.g., wait 12-47 minutes between posts).

### 3.2. Platform-Specific Strategies
**LinkedIn:**
*   **Discovery:** Use advanced boolean search (e.g., `"hiring" AND ("freelance" OR "contract") AND ("developer" OR "engineer")`). Filter by "Past 24 hours".
*   **Qualification:** Filter out promoted posts, agency recruiters (if desired), and posts with >20 comments to avoid saturated bids.
*   **Engagement Limit:** MAX 10-15 comments per day per account. Strict exponential backoff if a captcha or warning is encountered.

**X (Twitter):**
*   **Discovery:** Advanced search operators (`"looking for a developer" OR "hiring a freelancer" -filter:links min_faves:2`).
*   **Qualification:** Avoid bot threads, crypto spam, and engagement bait. AI must classify the tweet's intent before bidding.
*   **Engagement Limit:** MAX 5-10 replies per day.

### 3.3. AI Intelligence & Bid Generation
*   **Contextual RAG:** Inject the user's specific skills, portfolio links, and past projects into the LLM context to ensure bids are hyper-relevant.
*   **Multi-Step Reasoning Pipeline:**
    1.  **Intent Classification:** Does this post actually represent a legitimate job/freelance opportunity? (Yes/No).
    2.  **Pain Point Extraction:** What is the client's core problem or technical requirement?
    3.  **Drafting:** Generate a 2-3 sentence personalized pitch addressing the specific pain point, mentioning a relevant past project/tech stack, and ending with a soft Call-to-Action (CTA).
*   **Tone & Guardrails:** Must sound 100% human. NO corporate jargon. NO generic AI phrases ("As an AI", "I am a skilled developer", "I can help you with this").

### 3.4. Orchestration & Resilience (n8n)
*   **Idempotency & Deduplication:** PostgreSQL table `interactions` (schema: `id, platform, post_id, account_id, status, timestamp, ai_prompt, ai_response`). Before ANY action, the DB is checked to ensure no duplicate bids are ever made.
*   **Circuit Breakers:** If the Playwright script fails 3 times consecutively (e.g., UI changed, proxy failed, captcha loop), n8n must halt the workflow and fire a critical alert to Slack/Telegram.
*   **Error Handling:** Try-catch blocks on all API and DOM interaction nodes. Graceful fallback to OpenRouter if Groq rate limits.

## 4. Required Deliverables for Development
1.  **`docker-compose.yml`**: Full stack configuration (n8n, Postgres, Python worker API/container).
2.  **`src/python_workers/`**: Modular Python Playwright scripts:
    *   `stealth_browser.py`: Core browser initialization with evasion.
    *   `linkedin_agent.py`: LinkedIn specific navigation, search parsing, and DOM interactions.
    *   `x_agent.py`: Twitter specific navigation and interaction.
3.  **`n8n/workflows/`**: Advanced multi-stage workflows separating Discovery, Qualification, Drafting, Execution, and Auditing.
4.  **`prompts.yaml`**: Version-controlled, highly tuned system prompts for the LLM.

## 5. Non-Negotiable Constraints
*   **NO Official Posting APIs:** Do NOT use official APIs for posting (they are restricted/paid). It must be 100% UI automation to mimic real users.
*   **Headless execution:** Must be designed to run continuously on a Linux VPS (e.g., Hetzner/AWS) without manual GUI access, using Xvfb if necessary for extensions.
*   **Safety First:** Failing gracefully is better than getting banned. If unsure about a post or encountering unexpected UI elements, the bot must skip and log the anomaly.
