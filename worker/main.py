"""
main.py — FastAPI worker service.

Exposes internal HTTP endpoints called by n8n:
  POST /scrape      — Run platform scraper, return posts list
  POST /post        — Post a comment/reply on a specific post
  POST /reset       — Reset circuit breaker for a platform
  GET  /health      — Health check
  GET  /stats       — Live stats for dashboard

All endpoints protected by Bearer token (WORKER_SECRET).
"""
import asyncio
import random
from contextlib import asynccontextmanager
from datetime import datetime, time
from typing import Literal, Optional

import structlog
from fastapi import Depends, FastAPI, HTTPException, Security, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import text

import database as db
from ai_client import classify_intent, extract_pain_point, generate_bid
from alerting import alert_bid_posted, alert_circuit_tripped
from config import get_settings
from linkedin_agent import LinkedInAgent
from x_agent import XAgent

logger = structlog.get_logger(__name__)
settings = get_settings()

# ── Structured logging ────────────────────────────────────────────────────────
import logging
import structlog

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.dev.ConsoleRenderer() if settings.log_level == "DEBUG" else structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(
        getattr(logging, settings.log_level.upper(), logging.INFO)
    ),
    logger_factory=structlog.PrintLoggerFactory(),
)


# ── App lifecycle ─────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("worker.starting")
    # Verify DB connection
    try:
        async with db.get_db() as session:
            await session.execute(text("SELECT 1"))
        logger.info("worker.db_connected")
    except Exception as e:
        logger.critical("worker.db_connection_failed", error=str(e))
    yield
    logger.info("worker.shutting_down")


app = FastAPI(
    title="AutoBid Worker API",
    version="1.0.0",
    description="Playwright-powered LinkedIn & X automation worker",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Auth ──────────────────────────────────────────────────────────────────────
security = HTTPBearer()


def require_auth(creds: HTTPAuthorizationCredentials = Security(security)):
    if creds.credentials != settings.worker_secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return creds


# ── Request / Response Models ─────────────────────────────────────────────────
class ScrapeRequest(BaseModel):
    platform: Literal["linkedin", "x", "both"]
    keywords: Optional[list[str]] = None
    max_posts_per_keyword: int = 15


class PostRequest(BaseModel):
    platform: Literal["linkedin", "x"]
    post_id: str
    post_url: str
    post_content: str
    author_name: str


class ResetRequest(BaseModel):
    platform: Literal["linkedin", "x"]


# ── Helper: Business hours check ─────────────────────────────────────────────
async def _in_business_hours() -> bool:
    start_str = await db.get_config("business_hours_start", "09:00")
    end_str = await db.get_config("business_hours_end", "18:00")
    now_time = datetime.now().time()
    start = time(*map(int, start_str.split(":")))
    end = time(*map(int, end_str.split(":")))
    return start <= now_time <= end


# ── Helper: Rate limit check ──────────────────────────────────────────────────
async def _check_rate_limit(platform: str, account_id: str) -> tuple[bool, int]:
    limit_key = f"{platform}_daily_limit"
    default = "12" if platform == "linkedin" else "7"
    limit = int(await db.get_config(limit_key, default))
    count = await db.get_daily_count(account_id, platform)
    return count < limit, count


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@app.get("/stats")
async def stats(_: HTTPAuthorizationCredentials = Depends(require_auth)):
    """Live statistics for the dashboard."""
    async with db.get_db() as session:
        # Total interactions
        r = await session.execute(text("SELECT status, COUNT(*) FROM interactions GROUP BY status"))
        status_counts = {row[0]: row[1] for row in r.fetchall()}

        # Today's counts
        r = await session.execute(text(
            "SELECT platform, COUNT(*) FROM interactions "
            "WHERE created_at::date = CURRENT_DATE GROUP BY platform"
        ))
        today_counts = {row[0]: row[1] for row in r.fetchall()}

        # Circuit breakers
        r = await session.execute(text("SELECT platform, tripped, failure_count FROM circuit_breaker_state"))
        circuits = {row[0]: {"tripped": row[1], "failures": row[2]} for row in r.fetchall()}

        # Recent 5 posts
        r = await session.execute(text(
            "SELECT platform, author_name, status, created_at, post_url "
            "FROM interactions ORDER BY created_at DESC LIMIT 5"
        ))
        recent = [
            {"platform": row[0], "author": row[1], "status": row[2],
             "created_at": row[3].isoformat(), "url": row[4]}
            for row in r.fetchall()
        ]

    return {
        "status_counts": status_counts,
        "today_counts": today_counts,
        "circuit_breakers": circuits,
        "recent_interactions": recent,
        "business_hours": await _in_business_hours(),
    }


@app.post("/scrape")
async def scrape(req: ScrapeRequest, _: HTTPAuthorizationCredentials = Depends(require_auth)):
    """
    Scrape posts from LinkedIn and/or X.
    Returns list of new (non-duplicate) posts ready for bidding.
    """
    if not await _in_business_hours():
        logger.info("scrape.outside_business_hours")
        return {"posts": [], "skipped_reason": "outside_business_hours"}

    platforms = ["linkedin", "x"] if req.platform == "both" else [req.platform]
    all_posts = []

    for platform in platforms:
        # Check circuit breaker
        if await db.is_circuit_tripped(platform):
            logger.warning("scrape.circuit_tripped", platform=platform)
            continue

        # Get keywords
        keywords = req.keywords or await db.get_keywords(platform)

        for keyword in keywords[:5]:  # Max 5 keywords per run
            try:
                if platform == "linkedin":
                    agent = LinkedInAgent(
                        email=settings.linkedin_email,
                        password=settings.linkedin_password,
                        proxy_url=settings.proxy_url or None,
                    )
                    raw_posts = await agent.scrape_posts(keyword, req.max_posts_per_keyword)
                else:
                    agent = XAgent(
                        username=settings.x_username,
                        password=settings.x_password,
                        email=settings.x_email,
                        proxy_url=settings.proxy_url or None,
                    )
                    raw_posts = await agent.scrape_posts(keyword, req.max_posts_per_keyword)

                # Deduplicate against DB
                new_posts = []
                for post in raw_posts:
                    if not await db.is_duplicate(platform, post["post_id"]):
                        new_posts.append(post)
                    else:
                        logger.debug("scrape.duplicate_skipped", post_id=post["post_id"])

                all_posts.extend(new_posts)
                await db.reset_circuit(platform)  # Success — reset failures

            except Exception as e:
                logger.error("scrape.keyword_failed", keyword=keyword, platform=platform, error=str(e))
                tripped = await db.record_failure(
                    platform,
                    threshold=int(await db.get_config("circuit_breaker_threshold", "3")),
                )
                if tripped:
                    await alert_circuit_tripped(platform, failure_count=3)

            # Delay between keywords to appear natural
            await asyncio.sleep(random.uniform(5, 15))

    return {"posts": all_posts, "count": len(all_posts)}


@app.post("/post")
async def post_bid(req: PostRequest, _: HTTPAuthorizationCredentials = Depends(require_auth)):
    """
    Full pipeline: classify → extract pain point → generate bid → post → record.
    Called by n8n for each individual post.
    """
    platform = req.platform

    # ── Guard: Business hours
    if not await _in_business_hours():
        return {"success": False, "reason": "outside_business_hours"}

    # ── Guard: Circuit breaker
    if await db.is_circuit_tripped(platform):
        return {"success": False, "reason": "circuit_tripped"}

    # ── Guard: Deduplication
    if await db.is_duplicate(platform, req.post_id):
        return {"success": False, "reason": "already_posted"}

    # ── Guard: Daily rate limit
    account_id = (
        f"linkedin_{settings.linkedin_email.split('@')[0]}"
        if platform == "linkedin"
        else f"x_{settings.x_username}"
    )
    ok, count = await _check_rate_limit(platform, account_id)
    if not ok:
        logger.info("post.daily_limit_reached", platform=platform, count=count)
        return {"success": False, "reason": "daily_limit_reached", "count": count}

    # ── Step 1: Intent classification
    is_real = await classify_intent(req.post_content)
    if not is_real:
        await db.record_interaction(
            platform=platform, post_id=req.post_id, post_url=req.post_url,
            post_content=req.post_content, author_name=req.author_name,
            account_id=account_id, status="skipped",
            error_message="Not a genuine opportunity (intent classifier)",
        )
        return {"success": False, "reason": "not_a_real_opportunity"}

    # ── Step 2: Pain point extraction
    pain_point = await extract_pain_point(req.post_content)

    # ── Step 3: Bid generation
    bid_text, ai_prompt, model_used = await generate_bid(
        req.post_content, pain_point, platform
    )

    # ── Step 4: Post via Playwright
    success = False
    try:
        if platform == "linkedin":
            agent = LinkedInAgent(
                email=settings.linkedin_email,
                password=settings.linkedin_password,
                proxy_url=settings.proxy_url or None,
            )
            success = await agent.post_comment(req.post_url, bid_text)
        else:
            agent = XAgent(
                username=settings.x_username,
                password=settings.x_password,
                email=settings.x_email,
                proxy_url=settings.proxy_url or None,
            )
            success = await agent.post_reply(req.post_url, bid_text)

    except Exception as e:
        logger.error("post.playwright_failed", error=str(e))
        tripped = await db.record_failure(
            platform, threshold=int(await db.get_config("circuit_breaker_threshold", "3"))
        )
        if tripped:
            await alert_circuit_tripped(platform, failure_count=3)

    # ── Step 5: Record to DB
    final_status = "posted" if success else "failed"
    await db.record_interaction(
        platform=platform, post_id=req.post_id, post_url=req.post_url,
        post_content=req.post_content, author_name=req.author_name,
        account_id=account_id, status=final_status,
        ai_model=model_used, ai_prompt=ai_prompt,
        ai_response=bid_text, comment_posted=bid_text if success else None,
    )

    if success:
        await db.increment_daily_count(account_id, platform)
        await db.reset_circuit(platform)
        await alert_bid_posted(platform, req.author_name, bid_text)
    else:
        await db.record_failure(
            platform, threshold=int(await db.get_config("circuit_breaker_threshold", "3"))
        )

    return {
        "success": success,
        "bid_text": bid_text,
        "model_used": model_used,
        "pain_point": pain_point,
    }


@app.post("/reset")
async def reset_circuit(req: ResetRequest, _: HTTPAuthorizationCredentials = Depends(require_auth)):
    """Manually reset the circuit breaker for a platform."""
    await db.reset_circuit(req.platform)
    logger.info("circuit.reset_manually", platform=req.platform)
    return {"success": True, "platform": req.platform}
