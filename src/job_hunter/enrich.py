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
    r"(?:\s?(?:LPA|lpa|per year|per annum|/yr|a year|k))?"
    r"|[\d.]+\s?(?:-|–|to)?\s?[\d.]*\s?(?:LPA|lpa|lakhs?)\b",
    re.IGNORECASE,
)

_INSTRUCTIONS = """Return ONLY a JSON object:
{
  "about": string,           // 1-2 sentences on what the company does
  "salary": string,          // the TYPICAL / AVERAGE pay for THIS ROLE in THIS LOCATION,
                             // averaged across the many postings in the snippets (NOT one
                             // company). WITH currency/country, e.g. "avg ~₹12 LPA (INR),
                             // range ₹8-18 LPA". Prefix "est. ". For remote, state currency.
  "qualifications": string,  // key required qualifications, one line
  "work_culture": string,    // 1-2 sentences on culture, from employee reviews
  "pros": string,            // top positives from reviews, "; "-separated
  "cons": string,            // top negatives from reviews, "; "-separated
  "source": string           // where the info came from (sites/URLs or "job posting")
}"""

_DEEP_SYS = (
    "You are a company & compensation research analyst. From a job posting plus web "
    "search snippets, produce accurate, concise details.\n"
    "ACCURACY IS PARAMOUNT — NEVER invent or guess:\n"
    "- 'about' and 'qualifications': derive from the posting (always answerable).\n"
    "- 'salary': the snippets aggregate MANY people's reported pay for this role. "
    "Compute a representative AVERAGE and range for THIS ROLE in THIS LOCATION (a "
    "role's pay varies by city, so respect the location). Do NOT tie it to the one "
    "hiring company. WITH currency. If the snippets contain no salary data, return "
    "\"\" — do NOT estimate from general knowledge.\n"
    "- 'work_culture'/'pros'/'cons': ONLY from the review snippets. If none, return \"\".\n"
    "It is far better to leave a field empty than to fill it with something wrong. "
    "Respond with ONLY the JSON asked for."
)


def enrichment_prompt(job: Job) -> str:
    return (
        f"Job: {job.title} at {job.company}\n"
        f"Location: {job.location or 'unknown'}\n"
        f"Posting excerpt:\n{(job.description or '')[:2000]}\n\n"
        f"Research salary on Glassdoor/Levels.fyi/AmbitionBox and company culture / "
        f"pros / cons from employee reviews across sites.\n\n{_INSTRUCTIONS}"
    )


def apply_enrichment(job: Job, data: dict) -> Job:
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


def _salary_queries(job: Job) -> list[str]:
    """Several angles / sources — a mini deep-research sweep, not just Glassdoor."""
    loc = job.location or ""
    return [
        f"{job.title} {job.company} {loc} salary",                 # general web
        f"{job.title} {job.company} salary glassdoor OR levels.fyi",
        f"{job.title} {job.company} salary ambitionbox OR payscale OR indeed",
    ]


async def research_salary(context, job: Job, use_llm: bool = True) -> tuple[str | None, str | None]:
    """Web-research the salary across several sources. Returns (salary, source)."""
    results = await asyncio.gather(
        *(_search_snippets(context, q) for q in _salary_queries(job)),
        return_exceptions=True,
    )
    snippets = "\n".join(s for s in results if isinstance(s, str) and s.strip())
    if not snippets.strip():
        return None, None

    source = "web research (Glassdoor / Levels.fyi / AmbitionBox / Payscale)"
    if use_llm:
        try:
            from . import llm

            ans = llm.complete(
                "You are a compensation researcher. From these web search snippets "
                "(multiple sources), infer the most credible salary/compensation "
                "range for THIS role at THIS company/location. Prefer concrete "
                "figures; reconcile conflicting sources sensibly. If nothing "
                "usable is present, reply exactly 'unknown'. Otherwise reply with "
                "ONLY the range, prefixed 'est. '.",
                f"Role: {job.title} at {job.company} ({job.location or 'n/a'})\n"
                f"Snippets:\n{snippets[:4000]}",
                max_tokens=80,
            ).strip()
            if ans and "unknown" not in ans.lower():
                return ans, source
        except Exception:  # noqa: BLE001 — fall back to regex
            pass

    hit = salary_in_text(snippets)
    return (f"est. {hit}", source) if hit else (None, None)


def _llm_json_retry(system: str, prompt: str, max_tokens: int, attempts: int = 2) -> dict | None:
    """Call the LLM for JSON, retrying transient failures. Returns None if all fail."""
    from . import llm

    for _ in range(attempts):
        try:
            return llm.complete_json(system, prompt, max_tokens=max_tokens)
        except Exception:  # noqa: BLE001 — parse/network hiccup; retry then give up
            continue
    return None


def _deep_queries(job: Job) -> list[str]:
    """Salary queries are ROLE + LOCATION based (average many postings, not this one
    company); review queries are company-based for culture/pros/cons."""
    role = job.title
    loc = job.location or ""
    return [
        f"{role} average salary {loc} glassdoor",          # role+location salary...
        f"{role} salary {loc} ambitionbox payscale levels.fyi",
        f"{job.company} employee reviews work culture glassdoor ambitionbox",  # company culture
        f"{job.company} pros and cons working comparably indeed review",
    ]


async def enrich_with_browser(context, job: Job, profile=None, use_llm: bool = True) -> Job:
    """Gather FACTS only: role+location salary (averaged from the web) and company
    culture/pros/cons. Judgement (RAG + flags) is done later by the eligibility
    agent. `profile` is unused here now but kept for call-site compatibility."""
    results = await asyncio.gather(
        *(_search_snippets(context, q) for q in _deep_queries(job)),
        return_exceptions=True,
    )
    snippets = "\n".join(s for s in results if isinstance(s, str) and s.strip())

    if use_llm:
        prompt = (
            f"Role: {job.title}\nLocation: {job.location or 'unknown'}\n"
            f"Company: {job.company}\n"
            f"Posting excerpt:\n{(job.description or '')[:1800]}\n\n"
            f"Web search snippets (role salaries + company reviews):\n{snippets[:4000]}\n\n"
            f"{_INSTRUCTIONS}"
        )
        data = _llm_json_retry(_DEEP_SYS, prompt, max_tokens=800)
        if data:
            apply_enrichment(job, data)
            if not job.enrichment_source:
                job.enrichment_source = "web research (role salary avg + reviews)"
        # Guarantee posting-derived fields (always answerable from the description).
        if job.description and (not job.about or not job.qualifications):
            basic = _llm_json_retry(
                "Summarize from the posting only. Respond with ONLY JSON.",
                f"Job: {job.title} at {job.company}\nPosting:\n{job.description[:2500]}\n\n"
                'Return {"about": string, "qualifications": string}.',
                max_tokens=300,
            )
            if basic:
                job.about = job.about or basic.get("about")
                job.qualifications = job.qualifications or basic.get("qualifications")

    if not job.enriched:
        # No-LLM fallback: at least a salary from the posting or a regex web hit.
        posted_salary = salary_in_text(job.description)
        if posted_salary:
            job.salary, job.enrichment_source = posted_salary, "job posting"
        else:
            hit = salary_in_text(snippets)
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
