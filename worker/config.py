"""
config.py — Settings via Pydantic. Works with a .env file or real env vars.
Defaults are tuned for local single-process development (SQLite, no Redis).
"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root = two levels up from worker/
_ROOT = Path(__file__).parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Database ─────────────────────────────────────────────────────
    # SQLite by default — no server needed.
    # For Postgres set: DATABASE_URL=postgresql+asyncpg://user:pass@host/db
    database_url: str = f"sqlite+aiosqlite:///{_ROOT}/bidbot.db"

    # ── AI ───────────────────────────────────────────────────────────
    groq_api_key: str = ""
    openrouter_api_key: str = ""

    # ── Credentials ──────────────────────────────────────────────────
    linkedin_email: str = ""
    linkedin_password: str = ""
    x_username: str = ""
    x_password: str = ""
    x_email: str = ""

    # ── Proxy ────────────────────────────────────────────────────────
    proxy_url: str = ""

    # ── Security ─────────────────────────────────────────────────────
    worker_secret: str = "changeme"

    # ── Alerting ─────────────────────────────────────────────────────
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # ── Logging ──────────────────────────────────────────────────────
    log_level: str = "INFO"

    # ── Paths ────────────────────────────────────────────────────────
    sessions_dir: str = str(_ROOT / "sessions")
    screenshots_dir: str = str(_ROOT / "screenshots")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
