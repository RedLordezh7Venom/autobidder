"""
linkedin_agent.py — LinkedIn scraping and commenting via Playwright.

Flow:
  1. Login check / restore session
  2. Search for posts by keyword
  3. Filter: recency, comment count, relevance
  4. Return structured post list

Strict rate limiting and anti-detection throughout.
"""
import asyncio
import random
import re
from datetime import datetime
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

# LinkedIn advanced search URL
_SEARCH_URL = "https://www.linkedin.com/search/results/content/?keywords={query}&datePosted=past-24h&origin=FACETED_SEARCH&sortBy=date_posted"
_LOGIN_URL = "https://www.linkedin.com/login"
_FEED_URL = "https://www.linkedin.com/feed/"


class LinkedInAgent:
    def __init__(self, email: str, password: str, proxy_url: Optional[str] = None):
        self.email = email
        self.password = password
        self.account_id = f"linkedin_{email.split('@')[0]}"
        self.proxy_url = proxy_url
        self._browser: Optional[StealthBrowser] = None

    async def scrape_posts(self, keyword: str, max_posts: int = 20) -> list[dict]:
        """
        Log in (or reuse session) and scrape relevant posts for a keyword.
        Returns a list of dicts: {post_id, platform, content, url, author, comment_count}.
        """
        posts = []
        async with StealthBrowser(self.account_id, self.proxy_url) as browser:
            self._browser = browser
            page = await browser.new_page()

            try:
                # Check if we're already logged in
                logged_in = await self._check_session(page)
                if not logged_in:
                    await self._login(page)

                # Perform keyword search
                query = quote_plus(keyword)
                search_url = _SEARCH_URL.format(query=query)
                logger.info("linkedin.searching", keyword=keyword, url=search_url)
                await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
                await random_delay(2, 5)

                # Scroll to load more posts
                for _ in range(3):
                    await human_scroll(page, "down", random.randint(600, 1000))
                    await random_delay(1.5, 3.5)

                posts = await self._extract_posts(page, max_posts)
                logger.info("linkedin.scraped", keyword=keyword, count=len(posts))

            except Exception as e:
                logger.error("linkedin.scrape_failed", error=str(e), keyword=keyword)
                await browser.save_screenshot(page, f"error_scrape_{keyword[:20]}")
            finally:
                await page.close()

        return posts

    async def post_comment(self, post_url: str, comment: str) -> bool:
        """
        Navigate to a post and post a comment.
        Returns True on success.
        """
        async with StealthBrowser(self.account_id, self.proxy_url) as browser:
            page = await browser.new_page()
            try:
                logged_in = await self._check_session(page)
                if not logged_in:
                    await self._login(page)

                logger.info("linkedin.navigating_to_post", url=post_url)
                await page.goto(post_url, wait_until="domcontentloaded", timeout=30000)
                await random_delay(2, 5)

                # Scroll a bit before interacting (simulate reading)
                await human_scroll(page, "down", random.randint(200, 500))
                await random_delay(3, 8)

                # Find and click the comment box
                comment_box_selectors = [
                    "div.comments-comment-box__form-container",
                    "div[data-placeholder='Add a comment…']",
                    "div.ql-editor[data-placeholder='Add a comment…']",
                ]
                clicked = False
                for sel in comment_box_selectors:
                    try:
                        await human_click(page, sel, timeout=5000)
                        clicked = True
                        break
                    except Exception:
                        continue

                if not clicked:
                    # Try clicking the "Add a comment" button first
                    try:
                        await human_click(page, "button.comments-comment-box__trigger", timeout=5000)
                        await random_delay(1, 2)
                        for sel in comment_box_selectors:
                            try:
                                await human_click(page, sel, timeout=3000)
                                clicked = True
                                break
                            except Exception:
                                continue
                    except Exception:
                        pass

                if not clicked:
                    logger.warning("linkedin.comment_box_not_found", url=post_url)
                    await browser.save_screenshot(page, "comment_box_missing")
                    return False

                await random_delay(0.5, 1.5)

                # Type the comment character by character
                await human_type(page, "div.ql-editor[data-placeholder='Add a comment…']", comment)
                await random_delay(1, 3)

                # Click Post button
                post_btn_selectors = [
                    "button.comments-comment-box__submit-button",
                    "button[class*='submit-button']",
                ]
                submitted = False
                for sel in post_btn_selectors:
                    try:
                        await human_click(page, sel, timeout=5000)
                        submitted = True
                        break
                    except Exception:
                        continue

                if not submitted:
                    logger.warning("linkedin.submit_button_not_found", url=post_url)
                    await browser.save_screenshot(page, "submit_missing")
                    return False

                await random_delay(2, 4)
                await browser.save_screenshot(page, "comment_posted")
                logger.info("linkedin.comment_posted", url=post_url)
                return True

            except Exception as e:
                logger.error("linkedin.post_comment_failed", error=str(e), url=post_url)
                try:
                    await browser.save_screenshot(page, "comment_error")
                except Exception:
                    pass
                return False
            finally:
                await page.close()

    async def _check_session(self, page) -> bool:
        """Returns True if we're already logged in to LinkedIn."""
        try:
            await page.goto(_FEED_URL, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(2)
            # If we see the feed nav, we're logged in
            nav = await page.query_selector("nav.global-nav")
            return nav is not None
        except Exception:
            return False

    async def _login(self, page) -> None:
        """Full LinkedIn login flow with human-like typing."""
        logger.info("linkedin.logging_in")
        await page.goto(_LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
        await random_delay(1, 3)

        await human_type(page, "#username", self.email)
        await random_delay(0.5, 1.5)
        await human_type(page, "#password", self.password)
        await random_delay(0.5, 1.2)
        await human_click(page, "button[type='submit']")
        await random_delay(3, 7)

        # Wait for feed to load
        try:
            await page.wait_for_url("**/feed/**", timeout=15000)
            logger.info("linkedin.login_successful")
        except Exception:
            # Check for captcha or 2FA
            logger.warning("linkedin.login_may_need_verification")

    async def _extract_posts(self, page, max_posts: int) -> list[dict]:
        """Parse LinkedIn search results page for posts."""
        posts = []
        try:
            # LinkedIn post containers
            cards = await page.query_selector_all(
                "div.entity-result__item, div.feed-shared-update-v2"
            )
            if not cards:
                # Try alternative selector
                cards = await page.query_selector_all(
                    "[data-urn*='activity'], .search-content__entity-result"
                )

            for card in cards[:max_posts]:
                try:
                    post = await self._parse_post_card(page, card)
                    if post:
                        posts.append(post)
                except Exception as e:
                    logger.debug("linkedin.parse_card_failed", error=str(e))

        except Exception as e:
            logger.error("linkedin.extract_posts_failed", error=str(e))

        return posts

    async def _parse_post_card(self, page, card) -> Optional[dict]:
        """Extract data from a single post card element."""
        # Get post URL
        link = await card.query_selector("a[href*='activity']")
        if not link:
            link = await card.query_selector("a[href*='/posts/']")
        if not link:
            return None

        url = await link.get_attribute("href")
        if not url:
            return None
        if not url.startswith("http"):
            url = f"https://www.linkedin.com{url}"

        # Extract post ID from URL
        post_id_match = re.search(r"activity[:\-](\d+)", url)
        if not post_id_match:
            return None
        post_id = post_id_match.group(1)

        # Get content text
        content_el = await card.query_selector(
            ".feed-shared-update-v2__description, "
            ".entity-result__primary-subtitle, "
            "span.break-words"
        )
        content = ""
        if content_el:
            content = (await content_el.inner_text()).strip()

        # Get author name
        author_el = await card.query_selector(
            ".entity-result__title-text, "
            ".update-components-actor__name"
        )
        author = ""
        if author_el:
            author = (await author_el.inner_text()).strip()

        # Get comment count (skip posts with too many comments)
        comment_count = 0
        count_el = await card.query_selector(
            ".social-details-social-counts__comments"
        )
        if count_el:
            count_text = await count_el.inner_text()
            nums = re.findall(r"\d+", count_text)
            if nums:
                comment_count = int(nums[0])

        # Skip if saturated (>20 comments)
        if comment_count > 20:
            return None

        # Skip if content too short to be meaningful
        if len(content) < 30:
            return None

        return {
            "post_id": post_id,
            "platform": "linkedin",
            "content": content[:1000],
            "url": url,
            "author": author,
            "comment_count": comment_count,
        }
