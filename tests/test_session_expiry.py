"""Mid-run session-expiry detection (auth wall + vanished cookie)."""

from __future__ import annotations

from job_hunter.linkedin import browser


class _FakePage:
    def __init__(self, url: str):
        self.url = url


class _FakeContext:
    def __init__(self, cookies: list[dict]):
        self._cookies = cookies

    async def cookies(self, url: str):
        return self._cookies


def _session(url: str, cookies: list[dict]) -> browser.Session:
    s = browser.Session()
    s.page = _FakePage(url)
    s.context = _FakeContext(cookies)
    return s


LI_AT = [{"name": "li_at", "value": "valid-token"}]


async def test_auth_wall_by_url():
    s = _session("https://www.linkedin.com/checkpoint/challenge", LI_AT)
    assert await s.on_auth_wall() is True


async def test_auth_wall_by_missing_cookie():
    s = _session("https://www.linkedin.com/jobs/search/?keywords=x", [])
    assert await s.on_auth_wall() is True


async def test_healthy_session_is_not_a_wall():
    s = _session("https://www.linkedin.com/jobs/search/?keywords=x", LI_AT)
    assert await s.on_auth_wall() is False


async def test_headless_reauth_gives_up_immediately():
    s = browser.Session(headless=True)
    assert await s.await_reauth(timeout_s=5) is False


def test_session_expired_is_an_exception():
    assert issubclass(browser.SessionExpired, RuntimeError)
