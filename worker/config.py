"""
config.py — Centralized, type-safe application settings via Pydantic Settings.
All values come from environment variables / .env file.
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Database ──────────────────────────────────────────────
    database_url: str = "postgresql://bidbot:password@localhost:5432/bidding_bot"

    # ── Redis ─────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── AI ────────────────────────────────────────────────────
    groq_api_key: str = ""
    openrouter_api_key: str = ""

    # ── Credentials ───────────────────────────────────────────
    linkedin_email: str = ""
    linkedin_password: str = ""
    x_username: str = ""
    x_password: str = ""
    x_email: str = ""

    # ── Proxy ─────────────────────────────────────────────────
    proxy_url: str = ""

    # ── Security ──────────────────────────────────────────────
    worker_secret: str = "changeme"

    # ── Alerting ──────────────────────────────────────────────
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # ── Logging ───────────────────────────────────────────────
    log_level: str = "INFO"

    # ── Paths ─────────────────────────────────────────────────
    sessions_dir: str = "/app/sessions"
    screenshots_dir: str = "/app/screenshots"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
