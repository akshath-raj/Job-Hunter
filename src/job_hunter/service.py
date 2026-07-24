"""High-level operations shared by the CLI and the MCP server.

Each function is a complete unit of work (analyze resume, search, apply-batch)
and owns its own browser session where needed, so callers don't manage Playwright
lifecycles. Everything persists to the store as it goes, so a run can be stopped
and resumed.
"""

from __future__ import annotations

from . import constraints, store
from . import profile as profile_mod
from .apply.engine import apply_to_job
from .linkedin import browser, search
from .models import JobStatus, Profile
from .resume import analyze, extract

# ---- onboarding -----------------------------------------------------------

def ingest_resume(resume_path: str, description: str | None, use_llm: bool = True) -> Profile:
    """Extract + analyze a resume into the saved profile. Returns the profile.

    With use_llm=False (MCP path) it only extracts text and stores the path; the
    caller (Claude Code) then supplies the analysis via `set_analysis`.
    """
    from . import config

    text = extract.extract_text(resume_path)
    config.ensure_dirs()
    config.RESUME_TEXT_PATH.write_text(text)

    prof = profile_mod.load()
    prof.identity.resume_path = str(resume_path)
    if description:
        prof.description = description

    if use_llm:
        data = analyze.analyze(text, description)
        prof = analyze.apply_analysis(prof, data)

    profile_mod.save(prof)
    return prof


def resume_text() -> str:
    from . import config

    return config.RESUME_TEXT_PATH.read_text() if config.RESUME_TEXT_PATH.exists() else ""


def set_analysis(data: dict) -> Profile:
    """MCP path: apply an externally-produced resume analysis to the profile."""
    prof = analyze.apply_analysis(profile_mod.load(), data)
    profile_mod.save(prof)
    return prof


# ---- search ---------------------------------------------------------------

async def search_jobs(
    profile: Profile,
    queries: list[str] | None = None,
    max_per_query: int = 25,
    fetch_details: bool = True,
    headless: bool = False,
) -> dict:
    """Search LinkedIn for each query, store new jobs, tag eligibility."""
    queries = queries or profile.search_queries()
    if not queries:
        return {"error": "No search queries — analyze a resume or set target_roles first."}

    c = profile.constraints
    location = c.locations[0] if c.locations else profile.identity.location
    new_count = 0
    total = 0
    async with browser.session(headless=headless) as s:
        if not await s.is_logged_in():
            return {"error": "Not logged into LinkedIn. Run `job-hunter login` first."}
        for q in queries:
            jobs = await search.scrape_search(
                s, q, location=location, easy_apply=True,
                remote=c.remote_only, max_results=max_per_query,
            )
            for job in jobs:
                if fetch_details:
                    try:
                        job = await search.fetch_details(s, job)
                    except Exception:  # noqa: BLE001
                        pass
                constraints.annotate(job, profile)
                if store.upsert_job(job):
                    new_count += 1
                total += 1
    return {"queries": queries, "found": total, "new": new_count, **store.counts_by_status()}


# ---- apply ----------------------------------------------------------------

async def apply_batch(
    profile: Profile,
    limit: int = 10,
    use_llm: bool = True,
    headless: bool = False,
) -> dict:
    """Apply to up to `limit` eligible, not-yet-applied jobs."""
    eligible = [
        j for j in store.list_jobs(status=JobStatus.eligible)
        if not _already_applied(j.id)
    ][:limit]
    if not eligible:
        return {"applied": 0, "message": "No eligible pending jobs. Run a search first."}

    results = {"applied": 0, "needs_input": 0, "skipped": 0, "failed": 0, "details": []}
    async with browser.session(headless=headless) as s:
        if not await s.is_logged_in():
            return {"error": "Not logged into LinkedIn. Run `job-hunter login` first."}
        for job in eligible:
            app = await apply_to_job(s, job, profile, use_llm=use_llm)
            bucket = {
                JobStatus.applied: "applied",
                JobStatus.needs_input: "needs_input",
                JobStatus.skipped: "skipped",
                JobStatus.failed: "failed",
            }.get(app.status, "failed")
            results[bucket] += 1
            results["details"].append({
                "job": f"{job.title} @ {job.company}",
                "status": app.status.value,
                "prompt": app.needs_input_prompt,
                "error": app.error,
            })
    return results


def _already_applied(job_id: str) -> bool:
    app = store.get_application(job_id)
    return bool(app and app.status in {JobStatus.applied, JobStatus.skipped})


async def apply_single(profile: Profile, job_id: str, use_llm: bool = True,
                       headless: bool = False) -> dict:
    job = store.get_job(job_id)
    if not job:
        return {"error": f"Unknown job {job_id}"}
    async with browser.session(headless=headless) as s:
        if not await s.is_logged_in():
            return {"error": "Not logged into LinkedIn. Run `job-hunter login` first."}
        app = await apply_to_job(s, job, profile, use_llm=use_llm)
    return {"job": f"{job.title} @ {job.company}", "status": app.status.value,
            "prompt": app.needs_input_prompt, "error": app.error,
            "screenshot": app.screenshot_path}
