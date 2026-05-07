"""
x_agent.py — X (Twitter) scraping and replying via Playwright.

Uses X's Advanced Search UI to find posts.
No API required — pure browser automation.
"""
import asyncio
import random
import re
from typing import Optional
from urllib.parse import quote_plus

import structlog

from stealth_browser import (
    StealthBrowser,
    human_click,
    human_type,
    human_scroll,
    random_delay,
)

logger = structlog.get_logger(__name__)

_SEARCH_URL = "https://x.com/search?q={query}&f=live&src=typed_query"
_LOGIN_URL = "https://x.com/i/flow/login"
_HOME_URL = "https://x.com/home"


class XAgent:
    def __init__(
        self,
        username: str,
        password: str,
        email: str,
        proxy_url: Optional[str] = None,
    ):
        self.username = username
        self.password = password
        self.email = email
        self.account_id = f"x_{username}"
        self.proxy_url = proxy_url

    async def scrape_posts(self, keyword: str, max_posts: int = 15) -> list[dict]:
        """
        Search X for posts matching keyword.
        Returns list of post dicts.
        """
        posts = []
        async with StealthBrowser(self.account_id, self.proxy_url) as browser:
            page = await browser.new_page()
            try:
                logged_in = await self._check_session(page)
                if not logged_in:
                    await self._login(page, browser)

                query = quote_plus(keyword)
                url = _SEARCH_URL.format(query=query)
                logger.info("x.searching", keyword=keyword)
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await random_delay(2, 5)

                # Scroll to load posts
                for _ in range(3):
                    await human_scroll(page, "down", random.randint(400, 800))
                    await random_delay(1.5, 3.5)

                posts = await self._extract_posts(page, max_posts)
                logger.info("x.scraped", keyword=keyword, count=len(posts))

            except Exception as e:
                logger.error("x.scrape_failed", error=str(e), keyword=keyword)
                await browser.save_screenshot(page, f"x_error_{keyword[:15]}")
            finally:
                await page.close()

        return posts

    async def post_reply(self, post_url: str, reply: str) -> bool:
        """
        Open a tweet and post a reply.
        Returns True on success.
        """
        async with StealthBrowser(self.account_id, self.proxy_url) as browser:
            page = await browser.new_page()
            try:
                logged_in = await self._check_session(page)
                if not logged_in:
                    await self._login(page, browser)

                logger.info("x.navigating_to_post", url=post_url)
                await page.goto(post_url, wait_until="domcontentloaded", timeout=30000)
                await random_delay(3, 7)

                # Scroll slightly (simulate reading)
                await human_scroll(page, "down", random.randint(100, 300))
                await random_delay(2, 5)

                # Click "Reply" button
                reply_btn_selectors = [
                    "div[data-testid='reply']",
                    "button[aria-label='Reply']",
                ]
                clicked = False
                for sel in reply_btn_selectors:
                    try:
                        await human_click(page, sel, timeout=5000)
                        clicked = True
                        break
                    except Exception:
                        continue

                if not clicked:
                    logger.warning("x.reply_button_not_found", url=post_url)
                    await browser.save_screenshot(page, "x_reply_btn_missing")
                    return False

                await random_delay(1, 2.5)

                # Type reply in the reply box
                reply_box_selectors = [
                    "div[data-testid='tweetTextarea_0']",
                    "div[role='textbox'][data-contents='true']",
                ]
                typed = False
                for sel in reply_box_selectors:
                    try:
                        await human_type(page, sel, reply)
                        typed = True
                        break
                    except Exception:
                        continue

                if not typed:
                    logger.warning("x.reply_box_not_found", url=post_url)
                    await browser.save_screenshot(page, "x_reply_box_missing")
                    return False

                await random_delay(1, 3)

                # Click the "Reply" submit button
                submit_selectors = [
                    "div[data-testid='tweetButton']",
                    "div[data-testid='tweetButtonInline']",
                ]
                submitted = False
                for sel in submit_selectors:
                    try:
                        await human_click(page, sel, timeout=5000)
                        submitted = True
                        break
                    except Exception:
                        continue

                if not submitted:
                    logger.warning("x.submit_button_not_found")
                    await browser.save_screenshot(page, "x_submit_missing")
                    return False

                await random_delay(2, 4)
                await browser.save_screenshot(page, "x_reply_posted")
                logger.info("x.reply_posted", url=post_url)
                return True

            except Exception as e:
                logger.error("x.post_reply_failed", error=str(e), url=post_url)
                try:
                    await browser.save_screenshot(page, "x_reply_error")
                except Exception:
                    pass
                return False
            finally:
                await page.close()

    async def _check_session(self, page) -> bool:
        try:
            await page.goto(_HOME_URL, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(2)
            # If we see the compose button, we're logged in
            compose = await page.query_selector("a[data-testid='SideNav_NewTweet_Button']")
            return compose is not None
        except Exception:
            return False

    async def _login(self, page, browser: StealthBrowser) -> None:
        """X multi-step login flow: username → password (→ email verification if triggered)."""
        logger.info("x.logging_in")
        await page.goto(_LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
        await random_delay(2, 4)

        # Step 1: Enter username
        await human_type(page, "input[autocomplete='username']", self.username)
        await random_delay(0.5, 1.5)
        await human_click(page, "div[role='button']:has-text('Next')")
        await random_delay(2, 4)

        # Step 1b: X sometimes asks for email verification
        email_input = await page.query_selector("input[data-testid='ocfEnterTextTextInput']")
        if email_input:
            logger.info("x.email_verification_prompt")
            await human_type(page, "input[data-testid='ocfEnterTextTextInput']", self.email)
            await human_click(page, "div[role='button']:has-text('Next')")
            await random_delay(2, 4)

        # Step 2: Enter password
        await human_type(page, "input[name='password']", self.password)
        await random_delay(0.5, 1.5)
        await human_click(page, "div[data-testid='LoginForm_Login_Button']")
        await random_delay(4, 8)

        try:
            await page.wait_for_url("**/home**", timeout=20000)
            logger.info("x.login_successful")
        except Exception:
            await browser.save_screenshot(page, "x_login_failed")
            logger.warning("x.login_may_have_failed")

    async def _extract_posts(self, page, max_posts: int) -> list[dict]:
        posts = []
        try:
            tweet_cards = await page.query_selector_all("article[data-testid='tweet']")
            for card in tweet_cards[:max_posts]:
                try:
                    post = await self._parse_tweet_card(card)
                    if post:
                        posts.append(post)
                except Exception as e:
                    logger.debug("x.parse_card_failed", error=str(e))
        except Exception as e:
            logger.error("x.extract_failed", error=str(e))
        return posts

    async def _parse_tweet_card(self, card) -> Optional[dict]:
        """Extract data from a tweet article element."""
        # Get link to tweet
        link = await card.query_selector("a[href*='/status/']")
        if not link:
            return None
        url = await link.get_attribute("href")
        if not url:
            return None
        if not url.startswith("http"):
            url = f"https://x.com{url}"

        # Extract tweet ID
        id_match = re.search(r"/status/(\d+)", url)
        if not id_match:
            return None
        post_id = id_match.group(1)

        # Get tweet text
        text_el = await card.query_selector("div[data-testid='tweetText']")
        content = ""
        if text_el:
            content = (await text_el.inner_text()).strip()

        if len(content) < 20:
            return None

        # Get author
        author_el = await card.query_selector("div[data-testid='User-Name'] span")
        author = ""
        if author_el:
            author = (await author_el.inner_text()).strip()

        # Check reply count (skip if > 30 to avoid saturated threads)
        reply_count = 0
        reply_el = await card.query_selector("div[data-testid='reply'] span")
        if reply_el:
            count_text = await reply_el.inner_text()
            nums = re.findall(r"[\d,]+", count_text)
            if nums:
                reply_count = int(nums[0].replace(",", ""))

        if reply_count > 30:
            return None

        return {
            "post_id": post_id,
            "platform": "x",
            "content": content[:1000],
            "url": url,
            "author": author,
            "reply_count": reply_count,
        }
