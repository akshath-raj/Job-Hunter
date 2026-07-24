"""A persistent, human-paced browser session.

Design choices that matter for not getting your LinkedIn account flagged:
  * Persistent context bound to a real Chrome profile under JOBHUNTER_HOME, so
    you log in ONCE (and cookies/2FA survive across runs).
  * Non-headless by default — you can watch it, and CAPTCHAs are solvable.
  * `human_pause()` jitter between actions instead of machine-gun speed.

This same session is reused for LinkedIn search, Easy Apply, and any external
career site or Google Form we navigate to.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from .. import config

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


async def human_pause(lo: float = 0.6, hi: float = 1.8) -> None:
    await asyncio.sleep(random.uniform(lo, hi))


class Session:
    """Owns the Playwright context. Use via `async with Session() as s: s.page`."""

    def __init__(self, headless: bool = False):
        self.headless = headless
        self._pw = None
        self.context = None
        self.page = None

    async def __aenter__(self) -> Session:
        from playwright.async_api import async_playwright

        config.ensure_dirs()
        self._pw = await async_playwright().start()
        # channel="chrome" uses the real installed Chrome, which is far less
        # detectable than bundled Chromium. Falls back if unavailable.
        launch_kwargs = dict(
            user_data_dir=str(config.BROWSER_PROFILE_DIR),
            headless=self.headless,
            user_agent=_UA,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            self.context = await self._pw.chromium.launch_persistent_context(
                channel="chrome", **launch_kwargs
            )
        except Exception:  # noqa: BLE001 — chrome channel not present
            self.context = await self._pw.chromium.launch_persistent_context(**launch_kwargs)

        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        return self

    async def __aexit__(self, *exc) -> None:
        try:
            if self.context:
                await self.context.close()
        finally:
            if self._pw:
                await self._pw.stop()

    async def is_logged_in(self) -> bool:
        """True if the LinkedIn session is authenticated."""
        await self.page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
        await human_pause()
        return "/feed" in self.page.url and "login" not in self.page.url


@asynccontextmanager
async def session(headless: bool = False) -> AsyncIterator[Session]:
    s = Session(headless=headless)
    await s.__aenter__()
    try:
        yield s
    finally:
        await s.__aexit__(None, None, None)


async def ensure_login(headless: bool = False, timeout_s: int = 300) -> bool:
    """Open LinkedIn; if not logged in, wait (non-headless) for the user to sign in."""
    async with session(headless=headless) as s:
        if await s.is_logged_in():
            return True
        if headless:
            return False
        # Send the user to the login page and poll until authenticated.
        await s.page.goto("https://www.linkedin.com/login")
        waited = 0
        while waited < timeout_s:
            await asyncio.sleep(3)
            waited += 3
            if "/feed" in s.page.url or await s.is_logged_in():
                return True
        return False
