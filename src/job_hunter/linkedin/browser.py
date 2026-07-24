"""A persistent, human-paced browser session.

Design choices that matter for not getting your LinkedIn account flagged:
  * Real Chrome (channel="chrome"), non-headless by default — you can watch it
    and solve CAPTCHAs/2FA.
  * `human_pause()` jitter between actions instead of machine-gun speed.

Three profile modes (see config.py):
  * default  — isolated profile under JOBHUNTER_HOME; log in ONCE, no conflict
               with your everyday Chrome (Chrome locks a profile to one process).
  * CDP      — attach to a Chrome you already started with a debugging port, so
               it reuses your live login and real profile. We disconnect on exit
               WITHOUT closing your browser.
  * data-dir — launch against your real Chrome user-data-dir + named profile
               (Chrome must be fully closed first).

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

# URL fragments that mean "you're no longer authenticated on this page".
_AUTH_WALL_MARKERS = ("/login", "/checkpoint", "/authwall", "/uas/login", "/signup")


class SessionExpired(RuntimeError):
    """Raised mid-run when LinkedIn bounces us to a login/checkpoint wall."""


async def human_pause(lo: float = 0.6, hi: float = 1.8) -> None:
    await asyncio.sleep(random.uniform(lo, hi))


class Session:
    """Owns the Playwright context. Use via `async with Session() as s: s.page`."""

    def __init__(self, headless: bool = False):
        self.headless = headless
        self._pw = None
        self._browser = None          # set only in CDP mode
        self._owns_context = True      # False when attached to the user's browser
        self.context = None
        self.page = None

    async def __aenter__(self) -> Session:
        from playwright.async_api import async_playwright

        config.ensure_dirs()
        self._pw = await async_playwright().start()

        cdp = config.cdp_url()
        if cdp:
            await self._attach_cdp(cdp)
        else:
            await self._launch_persistent()

        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        return self

    async def _attach_cdp(self, cdp: str) -> None:
        """Reuse a Chrome the user already started with --remote-debugging-port."""
        self._browser = await self._pw.chromium.connect_over_cdp(cdp)
        self.context = (
            self._browser.contexts[0]
            if self._browser.contexts
            else await self._browser.new_context()
        )
        self._owns_context = False  # never close the user's own browser/tabs

    async def _launch_persistent(self) -> None:
        # channel="chrome" uses the real installed Chrome, which is far less
        # detectable than bundled Chromium. Falls back if unavailable.
        args = ["--disable-blink-features=AutomationControlled"]
        profile_dir = config.chrome_profile_directory()
        if profile_dir:
            args.append(f"--profile-directory={profile_dir}")
        launch_kwargs = dict(
            user_data_dir=config.chrome_user_data_dir(),
            headless=self.headless,
            user_agent=_UA,
            viewport={"width": 1280, "height": 900},
            args=args,
        )
        try:
            self.context = await self._pw.chromium.launch_persistent_context(
                channel="chrome", **launch_kwargs
            )
        except Exception:  # noqa: BLE001 — chrome channel not present
            self.context = await self._pw.chromium.launch_persistent_context(**launch_kwargs)

    async def __aexit__(self, *exc) -> None:
        try:
            if self._owns_context and self.context:
                await self.context.close()      # we launched it -> we close it
            elif self._browser:
                await self._browser.close()      # CDP: disconnect, leaves Chrome running
        finally:
            if self._pw:
                await self._pw.stop()

    async def has_auth_cookie(self) -> bool:
        """Passive login check — inspects the `li_at` auth cookie, no navigation.

        Safe to call while the user is typing on the login page (navigating there
        would interrupt them and cause a reload loop).
        """
        cookies = await self.context.cookies("https://www.linkedin.com")
        return any(c.get("name") == "li_at" and c.get("value") for c in cookies)

    async def is_logged_in(self) -> bool:
        """True if the LinkedIn session is authenticated.

        Prefers the passive cookie check; only navigates to /feed to confirm when
        no cookie is present (used to gate search/apply, not during login polling).
        """
        if await self.has_auth_cookie():
            return True
        await self.page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
        await human_pause()
        return "/feed" in self.page.url and "login" not in self.page.url

    async def on_auth_wall(self, page=None) -> bool:
        """True if a page got bounced to a login / security-checkpoint wall, or the
        auth cookie has vanished mid-run (session expired / challenged elsewhere)."""
        page = page or self.page
        url = (page.url or "").lower()
        if any(m in url for m in _AUTH_WALL_MARKERS):
            return True
        return not await self.has_auth_cookie()

    async def await_reauth(self, timeout_s: int = 180) -> bool:
        """Session died mid-run: wait (non-headless) for the user to re-sign-in.

        Sends the visible window to the login page and polls the cookie passively.
        Returns False immediately in headless mode (nobody can solve it).
        """
        if self.headless:
            return False
        try:
            await self.page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
        except Exception:  # noqa: BLE001
            pass
        waited = 0
        while waited < timeout_s:
            await asyncio.sleep(3)
            waited += 3
            if await self.has_auth_cookie():
                await human_pause(0.5, 1.0)
                return True
        return False


@asynccontextmanager
async def session(headless: bool = False) -> AsyncIterator[Session]:
    s = Session(headless=headless)
    await s.__aenter__()
    try:
        yield s
    finally:
        await s.__aexit__(None, None, None)


async def ensure_login(headless: bool = False, timeout_s: int = 300) -> bool:
    """Open LinkedIn; if not logged in, wait (non-headless) for the user to sign in.

    Polls the auth cookie WITHOUT navigating, so the user's login/2FA flow is
    never interrupted (navigating mid-login caused an endless reload loop).
    """
    async with session(headless=headless) as s:
        if await s.has_auth_cookie():
            return True
        if headless:
            return False
        # Land on the login page ONCE, then wait passively for the cookie.
        await s.page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
        waited = 0
        while waited < timeout_s:
            await asyncio.sleep(3)
            waited += 3
            if await s.has_auth_cookie():
                await human_pause(0.5, 1.0)  # let the session settle
                return True
        return False
