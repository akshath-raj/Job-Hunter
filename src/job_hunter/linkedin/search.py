"""Search LinkedIn Jobs and scrape listings into Job models.

LinkedIn's markup changes often, so selectors are defensive: several fallbacks
per field, and any card we can't parse is skipped rather than crashing the run.
We paginate by scrolling the results rail at a human pace.
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlencode

from ..models import Job
from .browser import Session, SessionExpired, human_pause

_JOBS_URL = "https://www.linkedin.com/jobs/search/"


def job_id(source: str, external_id: str) -> str:
    return hashlib.sha1(f"{source}:{external_id}".encode()).hexdigest()[:16]


def build_url(keywords: str, location: str | None = None, easy_apply: bool = True,
              remote: bool = False, date_posted_days: int | None = None,
              sort: str = "relevance") -> str:
    # Default to relevance across ALL postings (no date filter) — older jobs are
    # often the best matches. Pass sort="recent" / date_posted_days to narrow.
    params: dict[str, str] = {"keywords": keywords, "sortBy": "DD" if sort == "recent" else "R"}
    if location:
        params["location"] = location
    if easy_apply:
        params["f_AL"] = "true"          # Easy Apply only
    if remote:
        params["f_WT"] = "2"             # remote workplace type
    if date_posted_days:
        params["f_TPR"] = f"r{date_posted_days * 86400}"
    return f"{_JOBS_URL}?{urlencode(params)}"


async def _extract_external_id(card) -> str | None:
    for attr in ("data-occludable-job-id", "data-job-id"):
        val = await card.get_attribute(attr)
        if val:
            return val
    link = await card.query_selector("a[href*='/jobs/view/']")
    if link:
        href = await link.get_attribute("href") or ""
        m = re.search(r"/jobs/view/(\d+)", href)
        if m:
            return m.group(1)
    return None


async def _text(card, selectors: list[str]) -> str | None:
    for sel in selectors:
        el = await card.query_selector(sel)
        if el:
            txt = (await el.inner_text()).strip()
            if txt:
                return txt.split("\n")[0].strip()
    return None


async def scrape_search(
    s: Session,
    keywords: str,
    location: str | None = None,
    easy_apply: bool = True,
    remote: bool = False,
    max_results: int = 25,
    date_posted_days: int | None = None,   # None = all postings, not just recent
    sort: str = "relevance",
) -> list[Job]:
    url = build_url(keywords, location, easy_apply, remote, date_posted_days, sort)
    await s.page.goto(url, wait_until="domcontentloaded")
    await human_pause(1.5, 3.0)
    if await s.on_auth_wall():
        raise SessionExpired("LinkedIn session invalidated during search.")

    jobs: dict[str, Job] = {}
    stale_scrolls = 0
    while len(jobs) < max_results and stale_scrolls < 4:
        if await s.on_auth_wall():
            raise SessionExpired("LinkedIn session invalidated during search.")
        cards = await s.page.query_selector_all(
            "div.job-card-container, li.jobs-search-results__list-item, "
            "li.scaffold-layout__list-item"
        )
        before = len(jobs)
        for card in cards:
            ext = await _extract_external_id(card)
            if not ext:
                continue
            jid = job_id("linkedin", ext)
            if jid in jobs:
                continue
            title = await _text(card, [
                "a.job-card-list__title", "a.job-card-container__link",
                ".job-card-list__title--link", "strong",
            ])
            company = await _text(card, [
                ".job-card-container__primary-description",
                ".artdeco-entity-lockup__subtitle", ".job-card-container__company-name",
            ])
            loc = await _text(card, [
                ".job-card-container__metadata-item", ".job-card-container__metadata-wrapper",
            ])
            if not title:
                continue
            jobs[jid] = Job(
                id=jid, source="linkedin", external_id=ext,
                url=f"https://www.linkedin.com/jobs/view/{ext}/",
                title=title, company=company or "Unknown",
                location=loc, easy_apply=easy_apply,
            )
        # Scroll the results rail to load more.
        await s.page.mouse.wheel(0, 1600)
        await human_pause(1.0, 2.2)
        stale_scrolls = stale_scrolls + 1 if len(jobs) == before else 0

    return list(jobs.values())[:max_results]


async def fetch_details(s: Session, job: Job) -> Job:
    """Open a job page and fill in description, workplace type, Easy Apply flag."""
    await s.page.goto(job.url, wait_until="domcontentloaded")
    await human_pause(1.2, 2.5)

    desc_el = await s.page.query_selector(
        "div.jobs-description__content, article.jobs-description__container, #job-details"
    )
    if desc_el:
        job.description = (await desc_el.inner_text()).strip()[:8000]

    # Easy Apply detection.
    apply_btn = await s.page.query_selector("button.jobs-apply-button")
    if apply_btn:
        label = (await apply_btn.inner_text()).strip().lower()
        job.easy_apply = "easy apply" in label

    wp = await s.page.query_selector(
        ".jobs-unified-top-card__workplace-type, .job-details-jobs-unified-top-card__workplace-type"
    )
    if wp:
        job.workplace_type = (await wp.inner_text()).strip()

    # Posting age + applicant count live in the top-card description text; the
    # exact selectors churn, so read the whole strip and regex it out.
    top = await s.page.query_selector(
        ".job-details-jobs-unified-top-card__primary-description-container, "
        ".jobs-unified-top-card__primary-description, "
        ".job-details-jobs-unified-top-card__tertiary-description-container"
    )
    if top:
        text = " ".join((await top.inner_text()).split())
        m = re.search(r"(\d+\s+(?:hour|day|week|month)s?\s+ago|just now|today|yesterday)",
                      text, re.IGNORECASE)
        if m:
            job.posted_ago = m.group(1)
        m = re.search(r"(over\s+)?(\d[\d,]*)\s+(?:applicants?|people clicked apply)",
                      text, re.IGNORECASE)
        if m:
            job.num_applicants = m.group(0).strip()

    return job
