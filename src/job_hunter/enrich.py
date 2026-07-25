"""Enrich jobs with the details users want in the spreadsheet.

For each job we want: what the company does, the salary, and a summary of key
qualifications. When the posting doesn't state a salary, a research agent looks
it up on the web (Glassdoor/Levels.fyi and friends via a search engine) — these
run in parallel tabs during `search`, reusing the existing browser.

Two enrichment paths:
  * enrich_with_browser(context, job) — used standalone: reads salary from the
    posting, else web-searches it; company/qualifications via the LLM. Runs
    concurrently over many jobs.
  * enrichment_prompt / apply_enrichment — the MCP path: Claude Code runs the
    prompt as a cheap subagent WITH real web search, then posts results back.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from urllib.parse import quote_plus

from .models import Job

# Currency ranges, Indian LPA/lakhs, and "$120k/yr"-style figures.
SALARY_RE = re.compile(
    r"(?:₹|Rs\.?|\$|€|£)\s?[\d,]+(?:\.\d+)?"
    r"(?:\s?(?:-|–|to)\s?(?:₹|Rs\.?|\$|€|£)?\s?[\d,]+(?:\.\d+)?)?"
    r"(?:\s?(?:LPA|lpa|per year|per annum|/yr|a year|k|"
    r"per month|/month|/mo|a month|monthly|pm))?"
    r"|[\d.]+\s?(?:-|–|to)?\s?[\d.]*\s?(?:LPA|lpa|lakhs?)\b",
    re.IGNORECASE,
)


def is_intern(job: Job) -> bool:
    t = (job.title or "").lower()
    return any(w in t for w in ("intern", "internship", "trainee", "co-op", "apprentice"))

# Step 1 — extract from the LinkedIn job description FIRST (source of truth).
_JD_SYS = (
    "Extract details from a LinkedIn job posting ONLY. Use the posting text — do "
    "not use outside knowledge. For salary, report a figure ONLY if the posting "
    "explicitly states pay; otherwise return \"\". Never fabricate."
)
_JD_INSTRUCTIONS = """Return ONLY JSON:
{
  "summary": string,         // 2-4 sentence summary of the ROLE in your own words: what
                             // you'd actually do, key responsibilities, and what they want.
                             // Concise and skimmable — do NOT copy the posting verbatim.
  "about": string,           // what the company/team does, from the posting (1-2 sentences)
  "qualifications": string,  // required qualifications, summarized from the posting
  "salary": string           // pay WITH currency ONLY if the posting states it; else ""
}"""

# Step 2 — fill gaps from the web (salary averaged across many posts; reviews).
_WEB_SYS = (
    "You are a compensation & company-reviews analyst working from web search "
    "snippets. NEVER invent data.\n"
    "- 'salary': the snippets contain MANY people's reported pay for this role. "
    "Compute the AVERAGE across ALL of them for THIS ROLE in THIS LOCATION (pay "
    "varies by city) — do not rely on a single post or tie it to one company. "
    "Report avg + range WITH currency, prefixed 'est.'. If no salary data, \"\".\n"
    "- 'work_culture'/'pros'/'cons': ONLY from the employee-review snippets; \"\" if none.\n"
    "Respond with ONLY the JSON asked for."
)
_WEB_INSTRUCTIONS = """Return ONLY JSON:
{
  "salary": string,        // averaged role+location pay (see rules); "" if unknown
  "work_culture": string,  // 1-2 sentences from reviews; "" if none
  "pros": string,          // top positives from reviews, "; "-separated
  "cons": string           // top negatives from reviews, "; "-separated
}"""


def enrichment_prompt(job: Job) -> str:   # MCP prompt for Claude Code
    return (
        f"Job: {job.title} at {job.company}\nLocation: {job.location or 'unknown'}\n"
        f"Posting excerpt:\n{(job.description or '')[:2000]}\n\n"
        f"First take qualifications/salary from the posting. If salary isn't in the "
        f"posting, research the AVERAGE for this role+location (Glassdoor/AmbitionBox/"
        f"Levels.fyi). Take company culture/pros/cons from employee reviews.\n\n"
        f"{_WEB_INSTRUCTIONS}"
    )


def apply_enrichment(job: Job, data: dict) -> Job:
    """Merge enrichment data (MCP path) without clobbering existing values."""
    job.about = data.get("about") or job.about
    job.salary = data.get("salary") or job.salary
    job.qualifications = data.get("qualifications") or job.qualifications
    job.work_culture = data.get("work_culture") or job.work_culture
    job.pros = data.get("pros") or job.pros
    job.cons = data.get("cons") or job.cons
    job.enrichment_source = data.get("source") or job.enrichment_source
    job.enriched = True
    return job


def salary_in_text(text: str | None) -> str | None:
    if not text:
        return None
    m = SALARY_RE.search(text)
    return m.group(0).strip() if m else None


# Scrape-friendly search endpoints, tried in order until one returns text.
_ENGINES = [
    ("https://duckduckgo.com/html/?q={q}", ".result__snippet, .result__title"),
    ("https://html.duckduckgo.com/html/?q={q}", ".result__snippet, .result__title"),
    ("https://www.bing.com/search?q={q}", "li.b_algo, .b_caption p, .b_algo h2"),
    ("https://lite.duckduckgo.com/lite/?q={q}", "td, a"),
]


async def _serpapi_snippets(query: str) -> str:
    """Reliable Google results via SerpAPI (used when SERPAPI_KEY is set)."""
    key = os.environ.get("SERPAPI_KEY")
    if not key:
        return ""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get("https://serpapi.com/search.json", params={
                "q": query, "api_key": key, "engine": "google", "num": 10,
            })
            data = r.json()
    except Exception:  # noqa: BLE001 — network/quota; fall back to scraping
        return ""
    parts: list[str] = []
    box = data.get("answer_box") or {}
    if box.get("snippet"):
        parts.append(str(box["snippet"]))
    for res in (data.get("organic_results") or [])[:10]:
        parts.append(f"{res.get('title', '')} — {res.get('snippet', '')}".strip(" —"))
    return "\n".join(p for p in parts if p.strip())


async def _search_snippets(context, query: str) -> str:
    """Fetch result snippets — SerpAPI if configured, else scrape several engines."""
    serp = await _serpapi_snippets(query)
    if serp.strip():
        return serp
    for url_tpl, selector in _ENGINES:
        page = await context.new_page()
        try:
            await page.goto(url_tpl.format(q=quote_plus(query)), wait_until="domcontentloaded")
            els = await page.query_selector_all(selector)
            texts = [(await e.inner_text()).strip() for e in els[:10]]
            joined = "\n".join(t for t in texts if t)
            if joined.strip():
                return joined
        except Exception:  # noqa: BLE001 — engine blocked/unreachable, try next
            pass
        finally:
            await page.close()
    return ""


def _llm_json_retry(system: str, prompt: str, max_tokens: int, attempts: int = 2) -> dict | None:
    """Call the LLM for JSON, retrying transient failures. Returns None if all fail."""
    from . import llm

    for _ in range(attempts):
        try:
            return llm.complete_json(system, prompt, max_tokens=max_tokens)
        except Exception:  # noqa: BLE001 — parse/network hiccup; retry then give up
            continue
    return None


def _salary_queries(job: Job) -> list[str]:
    """ROLE + LOCATION salary queries — average many people's posts, not this one.
    Interns are paid a MONTHLY stipend, so query for that."""
    role, loc = job.title, (job.location or "")
    if is_intern(job):
        return [
            f"{role} intern stipend per month {loc}",
            f"{role} internship stipend {loc} glassdoor ambitionbox",
        ]
    return [
        f"{role} average salary {loc} glassdoor",
        f"{role} salary {loc} ambitionbox payscale levels.fyi",
    ]


def _pay_unit_hint(job: Job) -> str:
    return (
        "This is an INTERNSHIP — pay is a MONTHLY stipend. Report it PER MONTH "
        "(e.g. '₹25,000/month' or 'avg ₹30k/month'), NEVER per year/LPA.\n"
        if is_intern(job) else ""
    )


def _culture_queries(job: Job) -> list[str]:
    """Company-review queries for culture / pros / cons."""
    return [
        f"{job.company} employee reviews work culture glassdoor ambitionbox",
        f"{job.company} pros and cons working comparably indeed review",
    ]


async def enrich_with_browser(context, job: Job, profile=None, use_llm: bool = True) -> Job:
    """Facts, LinkedIn-JD-first. Step 1: about/qualifications/salary from the job
    description. Step 2: web ONLY for what the JD lacks — salary averaged across
    many role+location posts, and company culture/pros/cons from reviews."""
    # --- Step 1: the LinkedIn job description is the source of truth ------
    if use_llm and job.description:
        jd = _llm_json_retry(
            _JD_SYS,
            f"Job: {job.title} at {job.company}\nLocation: {job.location or 'n/a'}\n"
            f"{_pay_unit_hint(job)}"
            f"Posting:\n{job.description[:3000]}\n\n{_JD_INSTRUCTIONS}",
            max_tokens=500,
        )
        if jd:
            job.jd_summary = jd.get("summary") or job.jd_summary
            job.about = jd.get("about") or job.about
            job.qualifications = jd.get("qualifications") or job.qualifications
            if jd.get("salary"):
                job.salary = jd["salary"]
                job.enrichment_source = "LinkedIn job description"
    if not job.salary:                                  # regex on the JD as a backstop
        posted = salary_in_text(job.description)
        if posted:
            job.salary, job.enrichment_source = posted, "LinkedIn job description"

    # --- Step 2: web fills only the gaps ---------------------------------
    need_salary = not job.salary
    need_reviews = not (job.work_culture or job.pros or job.cons)
    if use_llm and (need_salary or need_reviews):
        queries = (_salary_queries(job) if need_salary else []) + \
                  (_culture_queries(job) if need_reviews else [])
        results = await asyncio.gather(
            *(_search_snippets(context, q) for q in queries), return_exceptions=True,
        )
        snippets = "\n".join(s for s in results if isinstance(s, str) and s.strip())
        if snippets.strip():
            web = _llm_json_retry(
                _WEB_SYS,
                f"Role: {job.title}\nLocation: {job.location or 'unknown'}\n"
                f"Company: {job.company}\n{_pay_unit_hint(job)}"
                f"Web snippets:\n{snippets[:4500]}\n\n{_WEB_INSTRUCTIONS}",
                max_tokens=600,
            )
            if web:
                if need_salary and web.get("salary"):
                    job.salary = web["salary"]
                    src = job.enrichment_source
                    job.enrichment_source = "web (role salary avg)" if not src else src
                job.work_culture = job.work_culture or web.get("work_culture")
                job.pros = job.pros or web.get("pros")
                job.cons = job.cons or web.get("cons")

    if not job.salary and not use_llm:                  # no-LLM: regex JD then web
        posted = salary_in_text(job.description)
        if posted:
            job.salary, job.enrichment_source = posted, "LinkedIn job description"
        else:
            snips = await asyncio.gather(
                *(_search_snippets(context, q) for q in _salary_queries(job)),
                return_exceptions=True,
            )
            hit = salary_in_text("\n".join(s for s in snips if isinstance(s, str)))
            if hit:
                job.salary, job.enrichment_source = f"est. {hit}", "web research"

    job.enriched = True
    return job


async def enrich(job: Job) -> Job:
    """Enrichment without a browser (LLM-only). Kept for the `enrich` CLI path."""
    from .agents import Complexity, research

    raw = await research(
        enrichment_prompt(job),
        complexity=Complexity.complex,
        system="You research companies and compensation using web search when the "
               "posting omits salary (prefer Glassdoor). Respond with ONLY the JSON.",
    )
    data = _parse_json(raw)
    if data:
        apply_enrichment(job, data)
    return job


def _parse_json(raw: str) -> dict | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None
