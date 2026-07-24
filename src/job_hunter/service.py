"""High-level operations shared by the CLI and the MCP server.

Each function is a complete unit of work (analyze resume, search, apply-batch)
and owns its own browser session where needed, so callers don't manage Playwright
lifecycles. Everything persists to the store as it goes, so a run can be stopped
and resumed.
"""

from __future__ import annotations

import asyncio

from . import constraints, relevance, store
from . import profile as profile_mod
from .apply.engine import apply_to_job
from .linkedin import browser, search
from .models import Job, JobStatus, Profile
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


def candidate_brief() -> str:
    """The detailed markdown brief the search agent uses, if it exists."""
    from . import config

    return config.BRIEF_PATH.read_text() if config.BRIEF_PATH.exists() else ""


def process_search_preferences(raw: dict[str, str]) -> dict:
    """LLM-process raw search answers into a structured strategy and save it.

    raw = {salary, locations, remote, additional}. Falls back to storing the raw
    answers directly when no LLM provider is configured.
    """
    from . import config, preferences

    prof = profile_mod.load()
    if config.has_llm():
        try:
            data = preferences.process(candidate_brief(), prof.target_roles, raw)
            preferences.apply_processed(prof, data)
            profile_mod.save(prof)
            _append_prefs_to_brief(prof, raw)
            return {"processed": True, "search_context": prof.search_context,
                    "search_keywords": prof.search_keywords,
                    "locations": prof.constraints.locations,
                    "exclude_keywords": prof.constraints.exclude_keywords}
        except Exception:  # noqa: BLE001 — fall back to raw storage
            pass
    profile_mod.set_search_preferences(
        prof, expected_salary=raw.get("salary") or None,
        locations=raw.get("locations") or None,
        remote_only=bool(raw.get("remote")),
        additional_details=raw.get("additional") or None,
    )
    profile_mod.save(prof)
    _append_prefs_to_brief(prof, raw)
    return {"processed": False, "search_context": prof.search_context,
            "search_keywords": prof.search_keywords}


def _append_prefs_to_brief(prof: Profile, raw: dict[str, str]) -> None:
    """Add the search Q&A + derived strategy to the brief (never overwrites it)."""
    lines = []
    if prof.search_context:
        lines.append(prof.search_context)
    answers = {
        "Expected salary": raw.get("salary"),
        "Preferred locations": raw.get("locations"),
        "Remote only": raw.get("remote"),
        "Additional details": raw.get("additional"),
    }
    kept = [f"- **{k}:** {v}" for k, v in answers.items() if v]
    if kept:
        lines.append("\n".join(["", "**Your answers:**", *kept]))
    if lines:
        analyze.update_brief_search_section("\n\n".join(lines))


# ---- search ---------------------------------------------------------------

async def search_jobs(
    profile: Profile,
    queries: list[str] | None = None,
    max_per_query: int = 25,
    fetch_details: bool = True,
    headless: bool = False,
    enrich: bool = True,
    concurrency: int = 3,
    export: bool = True,
    easy_apply_only: bool = False,
) -> dict:
    """Search LinkedIn, store new jobs, tag eligibility, enrich, and write Excel.

    Search is BROAD by default — it includes jobs with external/company-site
    applications, not just LinkedIn Easy Apply. Each job's real apply type is
    detected while scraping; the apply engine figures out how to submit each one.
    Set easy_apply_only=True to restrict to one-click Easy Apply.

    Enrichment (company/salary/qualifications) runs in parallel tabs on the same
    session — salary is web-searched when the posting omits it. `search` is the
    command that produces the spreadsheet.
    """
    from . import config
    from . import enrich as enrich_mod

    queries = queries or profile.search_queries()
    if not queries:
        return {"error": "No search queries — analyze a resume or set target_roles first."}

    c = profile.constraints
    location = c.locations[0] if c.locations else profile.identity.location
    new_jobs: list[Job] = []
    total = 0
    skipped_seen = 0
    # Track ids so we never re-visit a job: those already in the DB (prior runs)
    # plus those seen earlier in THIS run (a job can appear under many queries).
    seen_ids: set[str] = store.all_job_ids()

    async def _scrape(session, query):
        return await search.scrape_search(
            session, query, location=location, easy_apply=easy_apply_only,
            remote=c.remote_only, max_results=max_per_query,
        )

    async with browser.session(headless=headless) as s:
        if not await s.is_logged_in():
            return {"error": "Not logged into LinkedIn. Run `job-hunter login` first."}
        for q in queries:
            try:
                jobs = await _scrape(s, q)
            except browser.SessionExpired:
                # Session died mid-search. Wait for the user to re-login, then
                # retry this query once. Everything found so far is already saved.
                if not await s.await_reauth():
                    return {"error": "linkedin_session_expired", "new": len(new_jobs),
                            "message": "LinkedIn logged you out / showed a security check "
                                       "mid-search. Log back in and re-run — saved jobs are "
                                       "kept and it resumes.", **store.counts_by_status()}
                jobs = await _scrape(s, q)
            for job in jobs:
                total += 1
                # Skip anything we've already handled — no repeat page visits,
                # detail fetches, or enrichment for the same job id.
                if job.id in seen_ids:
                    skipped_seen += 1
                    continue
                seen_ids.add(job.id)
                if fetch_details:
                    try:
                        job = await search.fetch_details(s, job)
                    except Exception:  # noqa: BLE001
                        pass
                constraints.annotate(job, profile)
                relevance.annotate(job, profile)   # drop off-target jobs (keyword scorer)
                if store.upsert_job(job):
                    new_jobs.append(job)

        # LLM cross-check: catch off-field jobs the keyword scorer missed
        # (the "checking agent" that vets what goes into the spreadsheet).
        eligible_new = [j for j in new_jobs if j.status == JobStatus.eligible]
        off_target = relevance.llm_filter(profile, eligible_new, candidate_brief())
        for job in eligible_new:
            if job.id in off_target:
                job.status = JobStatus.ineligible
                job.ineligible_reason = "LLM check: outside your field/level"
                store.update_job(job)

        # Enrich the jobs that will actually be in the sheet (skip rejected ones).
        to_enrich = [j for j in new_jobs if j.status == JobStatus.eligible]
        if enrich and to_enrich:
            use_llm = config.has_llm()
            sem = asyncio.Semaphore(max(1, concurrency))

            async def worker(job: Job) -> None:
                async with sem:
                    await enrich_mod.enrich_with_browser(s.context, job, use_llm=use_llm)
                store.update_job(job)

            await asyncio.gather(*(worker(j) for j in to_enrich))

    result = {"queries": queries, "found": total, "new": len(new_jobs),
              "skipped_already_seen": skipped_seen, **store.counts_by_status()}
    if export:
        result["excel"] = export_excel()["path"]
    return result


# ---- apply ----------------------------------------------------------------

def _already_applied(job_id: str) -> bool:
    app = store.get_application(job_id)
    return bool(app and app.status in {JobStatus.applied, JobStatus.skipped})


def pending_eligible(limit: int | None = None) -> list[Job]:
    """Eligible jobs not yet applied to — the candidate pool for applying."""
    jobs = [j for j in store.list_jobs(status=JobStatus.eligible) if not _already_applied(j.id)]
    return jobs[:limit] if limit else jobs


def _bucket(status: JobStatus) -> str:
    return {
        JobStatus.applied: "applied",
        JobStatus.needs_input: "needs_input",
        JobStatus.skipped: "skipped",
        JobStatus.failed: "failed",
    }.get(status, "failed")


async def apply_batch(
    profile: Profile,
    limit: int = 10,
    mode: str = "auto",
    selected_ids: list[str] | None = None,
    use_llm: bool = True,
    headless: bool = False,
    concurrency: int = 2,
) -> dict:
    """Apply to jobs, concurrently, in one of two modes.

    mode="auto"   -> apply to up to `limit` eligible pending jobs.
    mode="select" -> apply only to `selected_ids` (the human-in-the-loop choice).

    `concurrency` runs several applications in parallel tabs; keep it modest to
    stay human-like and avoid tripping LinkedIn's anti-automation.
    """
    if mode == "select":
        if not selected_ids:
            return {"error": "select mode requires selected_ids (list_jobs / Excel to pick)."}
        jobs = [
            j for j in (store.get_job(i) for i in selected_ids)
            if j and not _already_applied(j.id)
        ]
    else:
        jobs = pending_eligible(limit)
    if not jobs:
        return {"applied": 0, "message": "No matching pending jobs. Search (and select) first."}

    results = {"applied": 0, "needs_input": 0, "skipped": 0, "failed": 0,
               "session_expired": 0, "details": []}
    sem = asyncio.Semaphore(max(1, concurrency))
    reauth_lock = asyncio.Lock()   # only one worker drives the re-login prompt
    aborted = {"flag": False}

    async with browser.session(headless=headless) as s:
        if not await s.is_logged_in():
            return {"error": "Not logged into LinkedIn. Run `job-hunter login` first."}

        async def ensure_session() -> bool:
            """True if authenticated (recovering via re-login once if needed)."""
            if not await s.on_auth_wall():
                return True
            async with reauth_lock:
                if not await s.on_auth_wall():   # another worker already fixed it
                    return True
                return await s.await_reauth()

        async def worker(job: Job) -> None:
            if aborted["flag"]:
                return
            # Guard BEFORE touching a real application — never fill a login page.
            if not await ensure_session():
                aborted["flag"] = True
                results["session_expired"] += 1
                results["details"].append({
                    "job_id": job.id, "job": f"{job.title} @ {job.company}",
                    "status": "needs_input",
                    "prompt": "LinkedIn session expired mid-run; re-login and re-run to resume.",
                })
                return
            async with sem:
                page = await s.context.new_page()
                try:
                    app = await apply_to_job(s.context, page, job, profile, use_llm=use_llm)
                finally:
                    await page.close()
            results[_bucket(app.status)] += 1
            results["details"].append({
                "job_id": job.id,
                "job": f"{job.title} @ {job.company}",
                "status": app.status.value,
                "prompt": app.needs_input_prompt,
                "error": app.error,
            })

        await asyncio.gather(*(worker(j) for j in jobs))
    if aborted["flag"]:
        results["message"] = ("Stopped early: LinkedIn logged you out / showed a security "
                              "check. Applied jobs are saved; re-run after logging in to resume.")
    return results


async def apply_single(profile: Profile, job_id: str, use_llm: bool = True,
                       headless: bool = False) -> dict:
    job = store.get_job(job_id)
    if not job:
        return {"error": f"Unknown job {job_id}"}
    async with browser.session(headless=headless) as s:
        if not await s.is_logged_in():
            return {"error": "Not logged into LinkedIn. Run `job-hunter login` first."}
        page = await s.context.new_page()
        try:
            app = await apply_to_job(s.context, page, job, profile, use_llm=use_llm)
        finally:
            await page.close()
    return {"job": f"{job.title} @ {job.company}", "status": app.status.value,
            "prompt": app.needs_input_prompt, "error": app.error,
            "screenshot": app.screenshot_path}


# ---- enrichment, export, memory -------------------------------------------

async def enrich_jobs(job_ids: list[str] | None = None, limit: int = 25,
                      concurrency: int = 3) -> dict:
    """Enrich jobs (company/salary/qualifications) via research sub-agents."""
    from . import enrich

    if job_ids:
        jobs = [j for j in (store.get_job(i) for i in job_ids) if j]
    else:
        jobs = [j for j in store.list_jobs() if not j.enriched][:limit]
    if not jobs:
        return {"enriched": 0, "message": "Nothing to enrich."}

    sem = asyncio.Semaphore(max(1, concurrency))

    async def worker(job: Job) -> None:
        async with sem:
            await enrich.enrich(job)
        store.update_job(job)

    await asyncio.gather(*(worker(j) for j in jobs))
    return {"enriched": len(jobs)}


def export_excel(status: JobStatus | None = None, path: str | None = None,
                 include_ineligible: bool = False) -> dict:
    """Write jobs to an .xlsx, best matches first. Off-target (ineligible/skipped)
    jobs are excluded by default so the sheet only shows relevant roles."""
    from . import export

    jobs = store.list_jobs(status=status, limit=1000)
    if status is None and not include_ineligible:
        jobs = [j for j in jobs if j.status not in {JobStatus.ineligible, JobStatus.skipped}]
    # Eligible/relevant first, then by match score (best fit at the top).
    jobs.sort(key=lambda j: (j.status != JobStatus.eligible, -(j.match_score or 0.0)))
    out = export.to_excel(jobs, path)
    return {"path": out, "rows": len(jobs)}


def remember_answers(answers: dict[str, str]) -> dict:
    """Persist {question: answer} to the ask-once memory so we never re-ask."""
    prof = profile_mod.load()
    profile_mod.apply_extra(prof, answers)
    profile_mod.save(prof)
    return {"saved": len(answers), "extra_keys": list(prof.extra.keys())}


# ---- data deletion --------------------------------------------------------

def clear_data(
    profile: bool = True,
    jobs: bool = True,
    session: bool = True,
    artifacts: bool = True,
    spreadsheet: bool = True,
) -> dict:
    """Delete locally-stored data about the user. Each flag is independent.

    profile     -> profile.json (identity, constraints, ask-once memory), resume
                   text, and candidate_brief.md (incl. search preferences)
    jobs        -> the jobs/applications database
    session     -> the LinkedIn browser session (logs you out)
    artifacts   -> submission screenshots + logs
    spreadsheet -> generated jobs.xlsx (cwd and home)
    """
    import shutil
    from pathlib import Path

    from . import config

    removed: list[str] = []

    def rm(path: Path) -> None:
        try:
            if path.is_dir():
                shutil.rmtree(path)
                removed.append(str(path))
            elif path.exists():
                path.unlink()
                removed.append(str(path))
        except Exception:  # noqa: BLE001 — best-effort deletion
            pass

    if profile:
        rm(config.PROFILE_PATH)
        rm(config.RESUME_TEXT_PATH)
        rm(config.BRIEF_PATH)          # candidate_brief.md (incl. search prefs section)
    if jobs:
        for p in (config.DB_PATH, Path(f"{config.DB_PATH}-wal"), Path(f"{config.DB_PATH}-shm")):
            rm(p)
    if session:
        rm(config.BROWSER_PROFILE_DIR)
    if artifacts:
        rm(config.ARTIFACTS_DIR)
        rm(config.LOG_DIR)
    if spreadsheet:
        rm(Path.cwd() / "jobs.xlsx")
        rm(config.HOME / "jobs.xlsx")

    return {"removed": removed, "count": len(removed)}
