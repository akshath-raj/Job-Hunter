"""Turn the user's raw search answers into a sharp, structured search strategy.

The user gives loose answers (salary "20 LPA", locations "blr or remote", plus a
free-text "anything else"). We pass those — together with the résumé brief — to
an LLM that acts as a recruiter and returns a clean strategy: refined LinkedIn
search keywords, normalized locations/salary, deal-breakers to exclude, and a
tight `search_context` paragraph the search agent reads before every run.

Standalone runs call `process()` (LLM). Under MCP, Claude Code produces the same
shape itself and stores it via the profile setter — no key needed.
"""

from __future__ import annotations

from typing import Any

from . import llm
from .models import Profile

SEARCH_AGENT_SYSTEM = """You are an elite technical recruiter and job-search \
strategist. You turn a candidate's real background and stated preferences into a \
razor-sharp LinkedIn search strategy.

You know that the same work is posted under many different titles (e.g. "ML \
Engineer" vs "Applied Scientist" vs "AI Engineer"; "Backend Engineer" vs \
"Platform Engineer"), and you translate what the candidate actually does into the \
exact titles employers post — never vague catch-alls like "Software Engineer" \
unless that truly fits. You are specific, decisive, and you respect the \
candidate's hard constraints (seniority, location, compensation, deal-breakers).

Your output drives an automated agent, so it must be clean and literal."""

PREF_INSTRUCTIONS = """Using the candidate brief and their answers, return ONLY a \
JSON object:
{
  "expected_salary": string|null,  // normalized, e.g. "20 LPA" or "$120k"; null if not given
  "locations": [string],           // clean list; include "Remote" if they want remote
  "remote_only": boolean,
  "refined_keywords": [string],    // 4-8 EXACT LinkedIn search strings tailored to this
                                   // candidate's specialization and preferences
  "exclude_keywords": [string],    // deal-breakers to filter out (e.g. "unpaid", "clearance",
                                   // company types they dislike) — [] if none
  "search_context": string         // 3-5 sentence brief for the search agent: what roles and
                                   // companies to prioritize, and what to avoid and why
}"""


def build_prompt(brief: str, target_roles: list[str], raw: dict[str, str]) -> str:
    return (
        f"CANDIDATE BRIEF:\n{brief or '(none)'}\n\n"
        f"Current target roles: {', '.join(target_roles) or '(none)'}\n\n"
        f"THEIR ANSWERS:\n"
        f"- Expected salary: {raw.get('salary') or '(not given)'}\n"
        f"- Preferred locations: {raw.get('locations') or '(not given)'}\n"
        f"- Remote only: {raw.get('remote') or 'no'}\n"
        f"- Additional details: {raw.get('additional') or '(none)'}\n\n"
        f"{PREF_INSTRUCTIONS}"
    )


def process(brief: str, target_roles: list[str], raw: dict[str, str]) -> dict[str, Any]:
    """LLM-process raw answers into a structured strategy (standalone path)."""
    return llm.complete_json(SEARCH_AGENT_SYSTEM, build_prompt(brief, target_roles, raw))


def apply_processed(profile: Profile, data: dict[str, Any]) -> Profile:
    """Merge a processed strategy into the profile."""
    c = profile.constraints

    if data.get("expected_salary"):
        from . import profile as profile_mod

        profile_mod.remember(profile, "expected salary", data["expected_salary"])
    if data.get("locations"):
        c.locations = [str(x) for x in data["locations"] if str(x).strip()]
    if data.get("remote_only") is not None:
        c.remote_only = bool(data["remote_only"])
    if data.get("refined_keywords"):
        # Refined keywords lead the search; keep prior ones as fallback, deduped.
        refined = [str(k) for k in data["refined_keywords"] if str(k).strip()]
        seen: set[str] = set()
        merged = []
        for k in refined + profile.search_keywords:
            if k.lower() not in seen:
                seen.add(k.lower())
                merged.append(k)
        profile.search_keywords = merged
    if data.get("exclude_keywords"):
        existing = {e.lower() for e in c.exclude_keywords}
        for e in data["exclude_keywords"]:
            if str(e).strip() and str(e).lower() not in existing:
                c.exclude_keywords.append(str(e))
    if data.get("search_context"):
        profile.search_context = str(data["search_context"]).strip()

    profile.search_prefs_collected = True
    return profile
