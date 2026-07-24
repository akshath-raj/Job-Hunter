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

_INSTRUCTIONS = """Return ONLY a JSON object:
{
  "about": string,           // 1-2 sentences on what the company does
  "salary": string,          // pay range WITH currency/country, e.g. "₹18-24 LPA (INR)"
                             // or "$120k-150k/yr (USD)". If NOT in the posting, research
                             // it (Glassdoor/Levels.fyi/AmbitionBox) and prefix "est. ".
                             // For REMOTE roles, state the currency explicitly.
  "qualifications": string,  // key required qualifications, one line
  "work_culture": string,    // 1-2 sentences on culture, from employee reviews
  "pros": string,            // top positives from reviews, "; "-separated
  "cons": string,            // top negatives from reviews, "; "-separated
  "source": string           // where the info came from (sites/URLs or "job posting")
}"""

_DEEP_SYS = (
    "You are a company & compensation research analyst. From a job posting plus web "
    "search snippets (salary sites and employee-review sites like Glassdoor, "
    "Levels.fyi, AmbitionBox, Indeed, Comparably), produce accurate, concise "
    "details. ALWAYS state salary with its currency/country and make the currency "
    "explicit for remote roles. Base culture/pros/cons on real employee reviews. "
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


def _deep_queries(job: Job) -> list[str]:
    """A mini deep-research sweep across salary AND employee-review sources."""
    loc = job.location or ""
    return [
        f"{job.title} {job.company} {loc} salary",
        f"{job.company} salary glassdoor levels.fyi ambitionbox payscale",
        f"{job.company} employee reviews work culture glassdoor ambitionbox indeed",
        f"{job.company} pros and cons working comparably review",
    ]


async def enrich_with_browser(context, job: Job, use_llm: bool = True) -> Job:
    """Deep enrichment: salary (with currency) + company culture/pros/cons, from
    the posting and several web sources, synthesized by the LLM."""
    posted_salary = salary_in_text(job.description)

    results = await asyncio.gather(
        *(_search_snippets(context, q) for q in _deep_queries(job)),
        return_exceptions=True,
    )
    snippets = "\n".join(s for s in results if isinstance(s, str) and s.strip())

    if use_llm:
        try:
            from . import llm

            prompt = (
                f"Job: {job.title} at {job.company}\n"
                f"Location: {job.location or 'unknown'} "
                f"(workplace: {job.workplace_type or 'n/a'})\n"
                f"Salary in posting: {posted_salary or 'not stated'}\n"
                f"Posting excerpt:\n{(job.description or '')[:1800]}\n\n"
                f"Web search snippets (salary + reviews):\n{snippets[:4000]}\n\n"
                f"{_INSTRUCTIONS}"
            )
            data = llm.complete_json(_DEEP_SYS, prompt, max_tokens=800)
            apply_enrichment(job, data)
            if not job.enrichment_source:
                job.enrichment_source = "web research (salary + review sites)"
            return job
        except Exception:  # noqa: BLE001 — fall back to non-LLM salary handling
            pass

    # No-LLM fallback: at least fill salary from the posting or a regex web hit.
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
