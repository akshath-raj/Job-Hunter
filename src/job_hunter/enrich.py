"""Enrich jobs with the details users want in the spreadsheet.

For each job we want: what the company does, the salary (from the posting or, if
absent, researched from the web), and a summary of key qualifications. The heavy
lifting is a research sub-agent (see agents.research) which runs on a cheap model
and can browse the web for salary data.

`enrichment_prompt` exposes the same task for the MCP path so Claude Code can run
it as a subagent with real web search and hand results back via
`apply_enrichment`.
"""

from __future__ import annotations

import json
from typing import Any

from .agents import Complexity, research
from .models import Job

_INSTRUCTIONS = """Return ONLY a JSON object:
{
  "about": string,           // 1-2 sentences on what the company does
  "salary": string,          // pay range; if not in the posting, research typical
                             // market pay for this role/location and prefix "est. "
  "qualifications": string,  // key required qualifications, one line
  "source": string           // where salary/about came from (URL or "job posting")
}"""


def enrichment_prompt(job: Job) -> str:
    return (
        f"Job: {job.title} at {job.company}\n"
        f"Location: {job.location or 'unknown'}\n"
        f"Posting excerpt:\n{(job.description or '')[:2000]}\n\n"
        f"{_INSTRUCTIONS}"
    )


def apply_enrichment(job: Job, data: dict[str, Any]) -> Job:
    job.about = data.get("about") or job.about
    job.salary = data.get("salary") or job.salary
    job.qualifications = data.get("qualifications") or job.qualifications
    job.enrichment_source = data.get("source") or job.enrichment_source
    job.enriched = True
    return job


async def enrich(job: Job) -> Job:
    """Standalone enrichment via the research sub-agent (best-effort)."""
    raw = await research(
        enrichment_prompt(job),
        complexity=Complexity.complex,
        system="You research companies and compensation. Use web search for "
               "salary when the posting omits it. Respond with ONLY the JSON asked for.",
    )
    data = _parse_json(raw)
    if data:
        apply_enrichment(job, data)
    return job


def _parse_json(raw: str) -> dict[str, Any] | None:
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
