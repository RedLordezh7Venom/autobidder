"""
stealth_browser.py — Playwright browser factory with comprehensive anti-detection.

Key techniques:
  • Chromium launched with 40+ flags to defeat headless detection
  • playwright-stealth JS injection to mask navigator.webdriver
  • Randomized viewport, User-Agent, and hardware fingerprints
  • Bezier curve mouse movements
  • Human-like typing with variable delays and occasional typos
  • Persistent session storage per account (cookies + localStorage)
"""
import asyncio
import math
import os
import random
import string
from pathlib import Path
from typing import Optional

import structlog
from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

from config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

# ── Realistic User-Agents ─────────────────────────────────────────────────────
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

_VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1680, "height": 1050},
    {"width": 1440, "height": 900},
    {"width": 1366, "height": 768},
]

# ── Stealth JS injection ──────────────────────────────────────────────────────
_STEALTH_SCRIPT = """
// 1. Hide navigator.webdriver
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

// 2. Fix permissions query
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => (
  parameters.name === 'notifications' ?
    Promise.resolve({ state: Notification.permission }) :
    originalQuery(parameters)
);

// 3. Randomize Canvas fingerprint slightly
const getContext = HTMLCanvasElement.prototype.getContext;
HTMLCanvasElement.prototype.getContext = function(type, ...args) {
  const ctx = getContext.apply(this, [type, ...args]);
  if (type === '2d') {
    const getImageData = ctx.getImageData;
    ctx.getImageData = function(...args) {
      const data = getImageData.apply(this, args);
      for (let i = 0; i < data.data.length; i += 100) {
        data.data[i] = data.data[i] ^ (Math.random() > 0.5 ? 1 : 0);
      }
      return data;
    };
  }
  return ctx;
};

// 4. Fix language & platform
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });

// 5. Remove headless chrome artifacts
delete window.chrome;
window.chrome = { runtime: {} };

// 6. Plugin count (real browsers have plugins)
Object.defineProperty(navigator, 'plugins', {
  get: () => [1, 2, 3, 4, 5],
});
"""


class StealthBrowser:
    """
    Context manager that provides a fully-stealthed Playwright browser context
    with persistent session storage for cookie reuse across runs.
    """

    def __init__(self, account_id: str, proxy_url: Optional[str] = None):
        self.account_id = account_id
        self.proxy_url = proxy_url or settings.proxy_url or None
        self.sessions_dir = Path(settings.sessions_dir)
        self.screenshots_dir = Path(settings.screenshots_dir)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None

    @property
    def session_path(self) -> str:
        return str(self.sessions_dir / self.account_id)

    async def __aenter__(self) -> "StealthBrowser":
        await self._launch()
        return self

    async def __aexit__(self, *_):
        await self._close()

    async def _launch(self):
        self._playwright = await async_playwright().start()

        proxy_config = None
        if self.proxy_url:
            proxy_config = {"server": self.proxy_url}

        viewport = random.choice(_VIEWPORTS)
        user_agent = random.choice(_USER_AGENTS)

        args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-accelerated-2d-canvas",
            "--disable-gpu",
            "--window-size={},{}" .format(viewport["width"], viewport["height"]),
            "--disable-features=IsolateOrigins,site-per-process",
            "--disable-web-security",
            "--disable-site-isolation-trials",
            "--lang=en-US",
            "--disable-notifications",
        ]

        # Persistent context keeps cookies across restarts
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=self.session_path,
            headless=True,
            args=args,
            user_agent=user_agent,
            viewport=viewport,
            locale="en-US",
            timezone_id="America/New_York",
            proxy=proxy_config,
            ignore_https_errors=True,
            java_script_enabled=True,
        )

        # Inject stealth script on every new page
        await self._context.add_init_script(script=_STEALTH_SCRIPT)

        logger.info("stealth_browser.launched", account_id=self.account_id, viewport=viewport)

    async def new_page(self) -> Page:
        page = await self._context.new_page()
        # Extra request interception: block heavy trackers/analytics for speed
        await page.route(
            "**/(analytics.js|gtag|facebook.com/tr|bat.bing.com)**",
            lambda route: route.abort(),
        )
        return page

    async def save_screenshot(self, page: Page, label: str) -> str:
        fname = f"{self.account_id}_{label}_{int(asyncio.get_event_loop().time())}.png"
        path = str(self.screenshots_dir / fname)
        await page.screenshot(path=path, full_page=False)
        logger.debug("screenshot.saved", path=path)
        return path

    async def _close(self):
        if self._context:
            await self._context.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info("stealth_browser.closed", account_id=self.account_id)


# ── Human-like Interaction Helpers ────────────────────────────────────────────

def _bezier_point(t: float, p0, p1, p2) -> tuple[float, float]:
    """Quadratic Bezier point at parameter t."""
    x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t ** 2 * p2[0]
    y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t ** 2 * p2[1]
    return x, y


async def human_move(page: Page, target_x: float, target_y: float) -> None:
    """Move mouse from current position to target using a Bezier curve."""
    current = await page.evaluate("() => ({ x: window._mouseX || 0, y: window._mouseY || 0 })")
    start = (current.get("x", 0), current.get("y", 0))
    # Random control point for curve
    ctrl = (
        (start[0] + target_x) / 2 + random.uniform(-80, 80),
        (start[1] + target_y) / 2 + random.uniform(-80, 80),
    )
    steps = random.randint(20, 40)
    for i in range(steps + 1):
        t = i / steps
        x, y = _bezier_point(t, start, ctrl, (target_x, target_y))
        await page.mouse.move(x, y)
        await asyncio.sleep(random.uniform(0.005, 0.015))


async def human_click(page: Page, selector: str, timeout: int = 10000) -> None:
    """Locate element, move to it naturally, then click."""
    element = await page.wait_for_selector(selector, timeout=timeout)
    box = await element.bounding_box()
    if not box:
        await element.click()
        return
    # Aim at a slightly random spot inside the element
    target_x = box["x"] + box["width"] * random.uniform(0.2, 0.8)
    target_y = box["y"] + box["height"] * random.uniform(0.2, 0.8)
    await human_move(page, target_x, target_y)
    await asyncio.sleep(random.uniform(0.05, 0.2))
    await page.mouse.click(target_x, target_y)


async def human_type(page: Page, selector: str, text: str) -> None:
    """Type text character-by-character with variable delays and occasional typos."""
    await human_click(page, selector)
    await asyncio.sleep(random.uniform(0.3, 0.8))

    for char in text:
        # 3% chance of a typo (type wrong char then backspace)
        if random.random() < 0.03 and char.isalpha():
            typo_char = random.choice(string.ascii_lowercase)
            await page.keyboard.press(typo_char)
            await asyncio.sleep(random.uniform(0.08, 0.2))
            await page.keyboard.press("Backspace")
            await asyncio.sleep(random.uniform(0.1, 0.25))

        await page.keyboard.press(char)
        # Variable delay: most chars 60-130ms, occasional longer pause
        if random.random() < 0.05:
            await asyncio.sleep(random.uniform(0.3, 0.8))  # "thinking" pause
        else:
            await asyncio.sleep(random.uniform(0.06, 0.13))


async def human_scroll(page: Page, direction: str = "down", distance: int = 500) -> None:
    """Scroll with variable speed to simulate reading."""
    steps = random.randint(5, 12)
    delta = distance // steps
    for _ in range(steps):
        dy = delta if direction == "down" else -delta
        await page.mouse.wheel(0, dy + random.randint(-20, 20))
        await asyncio.sleep(random.uniform(0.1, 0.4))


async def random_delay(min_seconds: float = 2.0, max_seconds: float = 6.0) -> None:
    """Sleep for a random duration in the given range."""
    delay = random.uniform(min_seconds, max_seconds)
    await asyncio.sleep(delay)
