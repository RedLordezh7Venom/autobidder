"""
alerting.py — Telegram alerting for circuit breaker trips and critical errors.
"""
import httpx
import structlog

from config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()


async def send_telegram_alert(message: str) -> bool:
    """Send a Telegram message to the configured chat. Returns True on success."""
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id

    if not token or not chat_id:
        logger.debug("telegram.not_configured")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": f"🤖 *AutoBid Bot Alert*\n\n{message}",
        "parse_mode": "Markdown",
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                logger.info("telegram.alert_sent")
                return True
            else:
                logger.warning("telegram.alert_failed", status=resp.status_code)
                return False
    except Exception as e:
        logger.error("telegram.send_error", error=str(e))
        return False


async def alert_circuit_tripped(platform: str, failure_count: int):
    await send_telegram_alert(
        f"⚡ *Circuit Breaker TRIPPED* for `{platform}`\n"
        f"Failures: {failure_count}\n"
        f"Action: Workflow halted. Check screenshots and logs."
    )


async def alert_daily_limit_reached(platform: str, account_id: str, count: int):
    await send_telegram_alert(
        f"📊 *Daily Limit Reached* for `{platform}`\n"
        f"Account: `{account_id}` | Count: {count}"
    )


async def alert_bid_posted(platform: str, author: str, bid_preview: str):
    await send_telegram_alert(
        f"✅ *Bid Posted* on `{platform}`\n"
        f"Author: {author}\n"
        f"Bid: _{bid_preview[:120]}..._"
    )
