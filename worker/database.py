"""
database.py — Async SQLAlchemy engine, session factory, and helper queries.
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import date
from typing import AsyncGenerator, Optional
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

# Convert postgresql:// → postgresql+asyncpg://
_db_url = settings.database_url.replace(
    "postgresql://", "postgresql+asyncpg://", 1
)

engine = create_async_engine(
    _db_url,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    echo=False,
)

AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


@asynccontextmanager
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ── Deduplication ──────────────────────────────────────────────────────────────

async def is_duplicate(platform: str, post_id: str) -> bool:
    """Return True if we have already interacted with this post."""
    async with get_db() as db:
        result = await db.execute(
            text("SELECT 1 FROM interactions WHERE platform=:p AND post_id=:pid LIMIT 1"),
            {"p": platform, "pid": post_id},
        )
        return result.fetchone() is not None


async def record_interaction(
    platform: str,
    post_id: str,
    post_url: str,
    post_content: str,
    author_name: str,
    account_id: str,
    status: str,
    ai_model: Optional[str] = None,
    ai_prompt: Optional[str] = None,
    ai_response: Optional[str] = None,
    comment_posted: Optional[str] = None,
    error_message: Optional[str] = None,
) -> UUID:
    """Insert a new interaction record. Returns the UUID."""
    async with get_db() as db:
        result = await db.execute(
            text("""
                INSERT INTO interactions
                    (platform, post_id, post_url, post_content, author_name,
                     account_id, status, ai_model, ai_prompt, ai_response,
                     comment_posted, error_message, posted_at)
                VALUES
                    (:platform, :post_id, :post_url, :post_content, :author_name,
                     :account_id, :status, :ai_model, :ai_prompt, :ai_response,
                     :comment_posted, :error_message,
                     CASE WHEN :status = 'posted' THEN NOW() ELSE NULL END)
                ON CONFLICT (platform, post_id)
                    DO UPDATE SET status = EXCLUDED.status, error_message = EXCLUDED.error_message
                RETURNING id
            """),
            {
                "platform": platform, "post_id": post_id, "post_url": post_url,
                "post_content": post_content, "author_name": author_name,
                "account_id": account_id, "status": status, "ai_model": ai_model,
                "ai_prompt": ai_prompt, "ai_response": ai_response,
                "comment_posted": comment_posted, "error_message": error_message,
            },
        )
        row = result.fetchone()
        return row[0]


# ── Daily Rate Limiting ────────────────────────────────────────────────────────

async def get_daily_count(account_id: str, platform: str) -> int:
    async with get_db() as db:
        result = await db.execute(
            text("""
                SELECT count FROM daily_counters
                WHERE account_id=:a AND platform=:p AND action_date=:d
            """),
            {"a": account_id, "p": platform, "d": date.today()},
        )
        row = result.fetchone()
        return row[0] if row else 0


async def increment_daily_count(account_id: str, platform: str) -> int:
    async with get_db() as db:
        result = await db.execute(
            text("""
                INSERT INTO daily_counters (account_id, platform, action_date, count)
                VALUES (:a, :p, :d, 1)
                ON CONFLICT (account_id, platform, action_date)
                    DO UPDATE SET count = daily_counters.count + 1
                RETURNING count
            """),
            {"a": account_id, "p": platform, "d": date.today()},
        )
        return result.fetchone()[0]


# ── Circuit Breaker ────────────────────────────────────────────────────────────

async def record_failure(platform: str, threshold: int = 3) -> bool:
    """Increment failure count. Returns True if circuit was just tripped."""
    async with get_db() as db:
        result = await db.execute(
            text("""
                UPDATE circuit_breaker_state
                SET failure_count = failure_count + 1,
                    last_failure_at = NOW(),
                    tripped = CASE WHEN failure_count + 1 >= :t THEN TRUE ELSE tripped END,
                    tripped_at = CASE WHEN failure_count + 1 >= :t AND NOT tripped THEN NOW() ELSE tripped_at END,
                    updated_at = NOW()
                WHERE platform = :p
                RETURNING tripped, failure_count
            """),
            {"p": platform, "t": threshold},
        )
        row = result.fetchone()
        return row[0] if row else False


async def reset_circuit(platform: str) -> None:
    async with get_db() as db:
        await db.execute(
            text("""
                UPDATE circuit_breaker_state
                SET failure_count=0, tripped=FALSE, updated_at=NOW()
                WHERE platform=:p
            """),
            {"p": platform},
        )


async def is_circuit_tripped(platform: str) -> bool:
    async with get_db() as db:
        result = await db.execute(
            text("SELECT tripped FROM circuit_breaker_state WHERE platform=:p"),
            {"p": platform},
        )
        row = result.fetchone()
        return row[0] if row else False


# ── Config ────────────────────────────────────────────────────────────────────

async def get_config(key: str, default: str = "") -> str:
    async with get_db() as db:
        result = await db.execute(
            text("SELECT value FROM system_config WHERE key=:k"),
            {"k": key},
        )
        row = result.fetchone()
        return row[0] if row else default


async def get_skills() -> list[dict]:
    async with get_db() as db:
        result = await db.execute(
            text("SELECT category, skill, proficiency FROM skills_profile ORDER BY category, skill")
        )
        return [{"category": r[0], "skill": r[1], "proficiency": r[2]} for r in result.fetchall()]


async def get_keywords(platform: str) -> list[str]:
    async with get_db() as db:
        result = await db.execute(
            text("SELECT keyword FROM keywords WHERE platform=:p AND enabled=TRUE"),
            {"p": platform},
        )
        return [r[0] for r in result.fetchall()]
