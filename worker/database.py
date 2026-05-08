"""
database.py — Async SQLAlchemy with SQLite (default) or Postgres.

All schema is created automatically on startup — no migrations needed for dev.
SQLite file lands at repo root as bidbot.db.
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import AsyncGenerator, Optional

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

# ── Engine ────────────────────────────────────────────────────────────────────
# SQLite needs check_same_thread=False and no pool sizing params.
_is_sqlite = settings.database_url.startswith("sqlite")

engine = create_async_engine(
    settings.database_url,
    echo=False,
    **({"connect_args": {"check_same_thread": False}} if _is_sqlite else {
        "pool_size": 10,
        "max_overflow": 20,
        "pool_pre_ping": True,
    }),
)

AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


# ── Schema bootstrap (runs once on startup) ───────────────────────────────────

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS interactions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    platform     TEXT NOT NULL,
    post_id      TEXT NOT NULL,
    post_url     TEXT,
    post_content TEXT,
    author_name  TEXT,
    account_id   TEXT,
    status       TEXT NOT NULL DEFAULT 'pending',
    ai_model     TEXT,
    ai_prompt    TEXT,
    ai_response  TEXT,
    comment_posted TEXT,
    error_message  TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    posted_at    TEXT,
    UNIQUE(platform, post_id)
);

CREATE TABLE IF NOT EXISTS daily_counters (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id  TEXT NOT NULL,
    platform    TEXT NOT NULL,
    action_date TEXT NOT NULL,
    count       INTEGER NOT NULL DEFAULT 0,
    UNIQUE(account_id, platform, action_date)
);

CREATE TABLE IF NOT EXISTS circuit_breaker_state (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    platform      TEXT NOT NULL UNIQUE,
    tripped       INTEGER NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0,
    last_failure_at TEXT,
    tripped_at    TEXT,
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS system_config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skills_profile (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category    TEXT NOT NULL,
    skill       TEXT NOT NULL,
    proficiency TEXT NOT NULL DEFAULT 'expert'
);

CREATE TABLE IF NOT EXISTS keywords (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    keyword  TEXT NOT NULL,
    enabled  INTEGER NOT NULL DEFAULT 1
);
"""

_SEED_SQL = """
-- Default circuit breaker rows
INSERT OR IGNORE INTO circuit_breaker_state (platform) VALUES ('linkedin');
INSERT OR IGNORE INTO circuit_breaker_state (platform) VALUES ('x');

-- Default config values
INSERT OR IGNORE INTO system_config VALUES ('linkedin_daily_limit', '12');
INSERT OR IGNORE INTO system_config VALUES ('x_daily_limit', '7');
INSERT OR IGNORE INTO system_config VALUES ('circuit_breaker_threshold', '3');
INSERT OR IGNORE INTO system_config VALUES ('business_hours_start', '09:00');
INSERT OR IGNORE INTO system_config VALUES ('business_hours_end', '18:00');
INSERT OR IGNORE INTO system_config VALUES ('groq_model', 'llama-3.3-70b-versatile');
INSERT OR IGNORE INTO system_config VALUES ('openrouter_model', 'anthropic/claude-3-haiku');
INSERT OR IGNORE INTO system_config VALUES ('ai_temperature', '0.75');

-- Demo skills (customize these)
INSERT OR IGNORE INTO skills_profile (category, skill, proficiency) VALUES
    ('backend',  'Python / FastAPI',    'expert'),
    ('backend',  'Node.js / Express',   'advanced'),
    ('frontend', 'React / Next.js',     'advanced'),
    ('ai',       'LangChain / RAG',     'expert'),
    ('ai',       'Groq / OpenAI APIs',  'expert'),
    ('devops',   'Docker / Kubernetes', 'intermediate'),
    ('database', 'PostgreSQL / SQLite', 'expert');

-- Demo keywords
INSERT OR IGNORE INTO keywords (platform, keyword) VALUES
    ('linkedin', 'looking for a Python developer'),
    ('linkedin', 'hiring a freelance developer'),
    ('linkedin', 'need a React developer'),
    ('x',        'looking for developer'),
    ('x',        'hiring freelancer'),
    ('x',        'need help with API');
"""


async def init_db() -> None:
    """Create all tables and seed defaults. Safe to call on every startup."""
    async with engine.begin() as conn:
        for stmt in _SCHEMA_SQL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                await conn.execute(text(stmt))
        for stmt in _SEED_SQL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                await conn.execute(text(stmt))
    logger.info("database.initialized", backend="sqlite" if _is_sqlite else "postgres")


# ── Session context ────────────────────────────────────────────────────────────

@asynccontextmanager
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ── Deduplication ─────────────────────────────────────────────────────────────

async def is_duplicate(platform: str, post_id: str) -> bool:
    async with get_db() as db:
        r = await db.execute(
            text("SELECT 1 FROM interactions WHERE platform=:p AND post_id=:pid LIMIT 1"),
            {"p": platform, "pid": post_id},
        )
        return r.fetchone() is not None


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
) -> int:
    posted_at = datetime.utcnow().isoformat() if status == "posted" else None
    async with get_db() as db:
        r = await db.execute(
            text("""
                INSERT INTO interactions
                    (platform, post_id, post_url, post_content, author_name,
                     account_id, status, ai_model, ai_prompt, ai_response,
                     comment_posted, error_message, posted_at)
                VALUES
                    (:platform, :post_id, :post_url, :post_content, :author_name,
                     :account_id, :status, :ai_model, :ai_prompt, :ai_response,
                     :comment_posted, :error_message, :posted_at)
                ON CONFLICT(platform, post_id)
                    DO UPDATE SET status=excluded.status,
                                  error_message=excluded.error_message
            """),
            {
                "platform": platform, "post_id": post_id, "post_url": post_url,
                "post_content": post_content, "author_name": author_name,
                "account_id": account_id, "status": status, "ai_model": ai_model,
                "ai_prompt": ai_prompt, "ai_response": ai_response,
                "comment_posted": comment_posted, "error_message": error_message,
                "posted_at": posted_at,
            },
        )
        return r.lastrowid


# ── Daily rate limiting ────────────────────────────────────────────────────────

async def get_daily_count(account_id: str, platform: str) -> int:
    async with get_db() as db:
        r = await db.execute(
            text("""
                SELECT count FROM daily_counters
                WHERE account_id=:a AND platform=:p AND action_date=:d
            """),
            {"a": account_id, "p": platform, "d": str(date.today())},
        )
        row = r.fetchone()
        return row[0] if row else 0


async def increment_daily_count(account_id: str, platform: str) -> int:
    today = str(date.today())
    async with get_db() as db:
        await db.execute(
            text("""
                INSERT INTO daily_counters (account_id, platform, action_date, count)
                VALUES (:a, :p, :d, 1)
                ON CONFLICT(account_id, platform, action_date)
                    DO UPDATE SET count = daily_counters.count + 1
            """),
            {"a": account_id, "p": platform, "d": today},
        )
        r = await db.execute(
            text("SELECT count FROM daily_counters WHERE account_id=:a AND platform=:p AND action_date=:d"),
            {"a": account_id, "p": platform, "d": today},
        )
        return r.fetchone()[0]


# ── Circuit breaker ───────────────────────────────────────────────────────────

async def record_failure(platform: str, threshold: int = 3) -> bool:
    now = datetime.utcnow().isoformat()
    async with get_db() as db:
        await db.execute(
            text("""
                UPDATE circuit_breaker_state
                SET failure_count = failure_count + 1,
                    last_failure_at = :now,
                    updated_at = :now
                WHERE platform = :p
            """),
            {"p": platform, "now": now},
        )
        r = await db.execute(
            text("SELECT failure_count FROM circuit_breaker_state WHERE platform=:p"),
            {"p": platform},
        )
        row = r.fetchone()
        count = row[0] if row else 0
        if count >= threshold:
            await db.execute(
                text("UPDATE circuit_breaker_state SET tripped=1, tripped_at=:now WHERE platform=:p"),
                {"p": platform, "now": now},
            )
            return True
        return False


async def reset_circuit(platform: str) -> None:
    now = datetime.utcnow().isoformat()
    async with get_db() as db:
        await db.execute(
            text("""
                UPDATE circuit_breaker_state
                SET failure_count=0, tripped=0, updated_at=:now
                WHERE platform=:p
            """),
            {"p": platform, "now": now},
        )


async def is_circuit_tripped(platform: str) -> bool:
    async with get_db() as db:
        r = await db.execute(
            text("SELECT tripped FROM circuit_breaker_state WHERE platform=:p"),
            {"p": platform},
        )
        row = r.fetchone()
        return bool(row[0]) if row else False


# ── Config & profile helpers ───────────────────────────────────────────────────

async def get_config(key: str, default: str = "") -> str:
    async with get_db() as db:
        r = await db.execute(
            text("SELECT value FROM system_config WHERE key=:k"),
            {"k": key},
        )
        row = r.fetchone()
        return row[0] if row else default


async def get_skills() -> list[dict]:
    async with get_db() as db:
        r = await db.execute(
            text("SELECT category, skill, proficiency FROM skills_profile ORDER BY category, skill")
        )
        return [{"category": row[0], "skill": row[1], "proficiency": row[2]} for row in r.fetchall()]


async def get_keywords(platform: str) -> list[str]:
    async with get_db() as db:
        r = await db.execute(
            text("SELECT keyword FROM keywords WHERE platform=:p AND enabled=1"),
            {"p": platform},
        )
        return [row[0] for row in r.fetchall()]
