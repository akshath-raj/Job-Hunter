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

_ABOUT_SYS = (
    "You summarize a company and a job's key qualifications from the posting and "
    "your own knowledge. Be factual and terse. Respond with ONLY the JSON asked for."
)
_ABOUT_INSTRUCTIONS = """Return ONLY: {"about": string, "qualifications": string}
- about: 1-2 sentences on what the company does.
- qualifications: the key required qualifications in one line."""

_INSTRUCTIONS = """Return ONLY a JSON object:
{
  "about": string,           // 1-2 sentences on what the company does
  "salary": string,          // pay range; if NOT in the posting, search the web
                             // (prefer Glassdoor / Levels.fyi) for the market
                             // range for this role+company+location, prefix "est. "
  "qualifications": string,  // key required qualifications, one line
  "source": string           // URL you got salary/about from, or "job posting"
}"""


def enrichment_prompt(job: Job) -> str:
    return (
        f"Job: {job.title} at {job.company}\n"
        f"Location: {job.location or 'unknown'}\n"
        f"Posting excerpt:\n{(job.description or '')[:2000]}\n\n"
        f"If the posting has no salary, search Glassdoor/Levels.fyi/Google for the "
        f"typical pay for this role at this company/location.\n\n{_INSTRUCTIONS}"
    )


def apply_enrichment(job: Job, data: dict) -> Job:
    job.about = data.get("about") or job.about
    job.salary = data.get("salary") or job.salary
    job.qualifications = data.get("qualifications") or job.qualifications
    job.enrichment_source = data.get("source") or job.enrichment_source
    job.enriched = True
    return job


def salary_in_text(text: str | None) -> str | None:
    if not text:
        return None
    m = SALARY_RE.search(text)
    return m.group(0).strip() if m else None


async def _search_snippets(context, query: str) -> str:
    """Fetch result snippets from a scrape-friendly search engine (DuckDuckGo)."""
    page = await context.new_page()
    try:
        await page.goto(
            f"https://duckduckgo.com/html/?q={quote_plus(query)}",
            wait_until="domcontentloaded",
        )
        els = await page.query_selector_all(".result__snippet, .result__title")
        texts = []
        for e in els[:8]:
            texts.append((await e.inner_text()).strip())
        return "\n".join(t for t in texts if t)
    except Exception:  # noqa: BLE001 — search blocked/unreachable
        return ""
    finally:
        await page.close()


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


async def enrich_with_browser(context, job: Job, use_llm: bool = True) -> Job:
    """Standalone enrichment: salary (posting -> web) + about/qualifications (LLM)."""
    if not job.salary:
        posted = salary_in_text(job.description)
        if posted:
            job.salary, job.enrichment_source = posted, "job posting"
        else:
            salary, source = await research_salary(context, job, use_llm)
            if salary:
                job.salary, job.enrichment_source = salary, source

    if use_llm and (not job.about or not job.qualifications):
        try:
            from . import llm

            data = llm.complete_json(
                _ABOUT_SYS,
                f"Job: {job.title} at {job.company}\n"
                f"Posting:\n{(job.description or '')[:2000]}\n\n{_ABOUT_INSTRUCTIONS}",
                max_tokens=300,
            )
            job.about = job.about or data.get("about")
            job.qualifications = job.qualifications or data.get("qualifications")
        except Exception:  # noqa: BLE001
            pass

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
