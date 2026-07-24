"""MCP server exposing Job Hunter to Claude Code (or any MCP client).

In this mode Claude Code is the "brain": it calls these tools to onboard, search,
and apply — and for the resume analysis it can reason itself via
`analyze_resume_prompt` (no API key needed). For genuinely novel career sites or
Google Forms, pair this with the Playwright MCP: Claude drives the browser
directly with full page context, then records the outcome here.

Run:  job-hunter-mcp        (stdio transport)
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from . import profile as profile_mod
from . import service, store
from .linkedin import browser
from .models import Constraints, JobStatus, Seniority
from .resume import analyze, extract

mcp = FastMCP("job-hunter")


# ---- profile & onboarding -------------------------------------------------

@mcp.tool()
def get_profile() -> dict[str, Any]:
    """Return the saved user profile (identity, constraints, target roles, skills)."""
    return profile_mod.load().model_dump()


@mcp.tool()
def analyze_resume_prompt(resume_path: str, description: str = "") -> dict[str, Any]:
    """Extract resume text and return an analysis prompt.

    Claude should read `resume_text`, produce the JSON described by
    `instructions`, then call `save_resume_analysis` with it. This is how role
    understanding happens without an Anthropic API key.
    """
    text = extract.extract_text(resume_path)
    from . import config

    config.ensure_dirs()
    config.RESUME_TEXT_PATH.write_text(text)
    prof = profile_mod.load()
    prof.identity.resume_path = resume_path
    if description:
        prof.description = description
    profile_mod.save(prof)
    return {
        "resume_text": text[:12000],
        "system": analyze.ANALYSIS_SYSTEM,
        "instructions": analyze.ANALYSIS_INSTRUCTIONS,
        "next": "Call save_resume_analysis with the JSON you produce.",
    }


@mcp.tool()
def save_resume_analysis(analysis: dict[str, Any]) -> dict[str, Any]:
    """Persist a resume analysis (the JSON from analyze_resume_prompt) to the profile."""
    prof = service.set_analysis(analysis)
    missing = profile_mod.missing_required_fields(prof, include_recommended=True)
    return {"saved": True, "profile": prof.model_dump(), "still_missing": missing}


@mcp.tool()
def missing_profile_fields() -> dict[str, str]:
    """Return {field: question} for required details still unknown. Ask the user these."""
    return profile_mod.missing_required_fields(profile_mod.load(), include_recommended=True)


@mcp.tool()
def set_profile_fields(fields: dict[str, str]) -> dict[str, Any]:
    """Set profile fields by dotted path, e.g. {"identity.phone": "..."}."""
    prof = profile_mod.apply_answers(profile_mod.load(), fields)
    profile_mod.save(prof)
    return {"saved": True, "profile": prof.model_dump()}


@mcp.tool()
def set_constraints(
    is_student: bool | None = None,
    max_seniority: str | None = None,
    remote_only: bool | None = None,
    locations: list[str] | None = None,
    require_sponsorship: bool | None = None,
    work_authorization: str | None = None,
    exclude_companies: list[str] | None = None,
    exclude_keywords: list[str] | None = None,
    min_salary: int | None = None,
) -> dict[str, Any]:
    """Update hard eligibility rules. Only provided fields change.

    Example: a college student -> set is_student=true (auto-caps seniority to entry).
    """
    prof = profile_mod.load()
    c: Constraints = prof.constraints
    if is_student is not None:
        c.is_student = is_student
        if is_student and c.max_seniority is None:
            c.max_seniority = Seniority.entry
    if max_seniority is not None:
        c.max_seniority = Seniority(max_seniority)
    if remote_only is not None:
        c.remote_only = remote_only
    if locations is not None:
        c.locations = locations
    if require_sponsorship is not None:
        c.require_sponsorship = require_sponsorship
    if work_authorization is not None:
        c.work_authorization = work_authorization
    if exclude_companies is not None:
        c.exclude_companies = exclude_companies
    if exclude_keywords is not None:
        c.exclude_keywords = exclude_keywords
    if min_salary is not None:
        c.min_salary = min_salary
    profile_mod.save(prof)
    return {"saved": True, "constraints": c.model_dump()}


# ---- linkedin -------------------------------------------------------------

@mcp.tool()
async def login_linkedin() -> dict[str, Any]:
    """Open a browser so the user can log into LinkedIn (session persists)."""
    ok = await browser.ensure_login(headless=False)
    return {"logged_in": ok}


@mcp.tool()
async def search_jobs(queries: list[str] | None = None, max_per_query: int = 25) -> dict[str, Any]:
    """Search LinkedIn for jobs matching the profile; stores new ones, tags eligibility."""
    return await service.search_jobs(
        profile_mod.load(), queries=queries, max_per_query=max_per_query
    )


@mcp.tool()
def list_jobs(status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """List stored jobs. status: discovered|eligible|ineligible|applied|needs_input|failed."""
    st = JobStatus(status) if status else None
    return [j.model_dump() for j in store.list_jobs(status=st, limit=limit)]


@mcp.tool()
def job_status_counts() -> dict[str, int]:
    """Return a count of jobs per status."""
    return store.counts_by_status()


# ---- apply ----------------------------------------------------------------

@mcp.tool()
async def apply_to_job(job_id: str, use_llm: bool = True) -> dict[str, Any]:
    """Apply to one stored job (eligibility gate enforced). Returns result + any needed input."""
    return await service.apply_single(profile_mod.load(), job_id, use_llm=use_llm)


@mcp.tool()
async def apply_batch(limit: int = 10, use_llm: bool = True) -> dict[str, Any]:
    """Autonomously apply to up to `limit` eligible pending jobs."""
    return await service.apply_batch(profile_mod.load(), limit=limit, use_llm=use_llm)


@mcp.tool()
def pending_input_jobs() -> list[dict[str, Any]]:
    """Jobs paused waiting on the user (a question we couldn't answer, a CAPTCHA, etc.)."""
    out = []
    for app in store.list_applications(status=JobStatus.needs_input):
        job = store.get_job(app.job_id)
        out.append({
            "job_id": app.job_id,
            "job": f"{job.title} @ {job.company}" if job else app.job_id,
            "prompt": app.needs_input_prompt,
            "method": app.method,
        })
    return out


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
