"""Apply to a single job, end to end, with the eligibility gate enforced first.

Flow:
  1. Skip if already applied (dedup).
  2. Hard eligibility check — student-vs-senior etc. Ineligible => skip, logged.
  3. Route: LinkedIn Easy Apply -> external Google Form -> external generic site.
  4. Persist an Application (status, answer log, screenshot) either way.

Nothing here submits a job that fails `constraints.check`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from .. import config, constraints, store
from ..linkedin import easy_apply
from ..linkedin.browser import Session, human_pause
from ..models import Application, Job, JobStatus, Profile
from . import forms
from .answerer import ProfileAnswerer


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def _screenshot(page, job: Job) -> str | None:
    try:
        path = config.ARTIFACTS_DIR / f"{job.id}.png"
        config.ensure_dirs()
        await page.screenshot(path=str(path), full_page=False)
        return str(path)
    except Exception:  # noqa: BLE001
        return None


async def apply_to_job(
    s: Session, job: Job, profile: Profile, use_llm: bool = True
) -> Application:
    existing = store.get_application(job.id)
    if existing and existing.status == JobStatus.applied:
        return existing

    # --- eligibility gate -------------------------------------------------
    ok, reason = constraints.check(job, profile)
    if not ok:
        job.status = JobStatus.ineligible
        job.ineligible_reason = reason
        store.update_job(job)
        app = Application(job_id=job.id, status=JobStatus.skipped, notes=f"ineligible: {reason}")
        store.save_application(app)
        return app

    job.status = JobStatus.applying
    store.update_job(job)
    answerer = ProfileAnswerer(profile, job=job, use_llm=use_llm)

    # --- route ------------------------------------------------------------
    if job.easy_apply:
        app = await _easy_apply(s, job, answerer)
    else:
        app = await _external_apply(s, job, profile, answerer)

    job.status = app.status
    store.update_job(job)
    store.save_application(app)
    return app


async def _easy_apply(s: Session, job: Job, answerer: ProfileAnswerer) -> Application:
    res = await easy_apply.apply(s, job.url, answerer)
    app = Application(job_id=job.id, method="easy_apply", answers=res.answers)
    if res.submitted:
        app.status = JobStatus.applied
        app.submitted_at = _now()
        app.screenshot_path = await _screenshot(s.page, job)
    elif res.needs_input:
        app.status = JobStatus.needs_input
        app.needs_input_prompt = res.prompt
    else:
        app.status = JobStatus.failed
        app.error = res.error
    return app


async def _external_apply(
    s: Session, job: Job, profile: Profile, answerer: ProfileAnswerer
) -> Application:
    app = Application(job_id=job.id, method="external_form")
    page = s.page
    await page.goto(job.url, wait_until="domcontentloaded")
    await human_pause(1.0, 2.0)

    apply_btn = await page.query_selector("button.jobs-apply-button, a.jobs-apply-button")
    if not apply_btn:
        app.status = JobStatus.needs_input
        app.needs_input_prompt = "Couldn't find the external apply button on the LinkedIn posting."
        return app

    # External "Apply" usually opens the company site in a new tab.
    new_page = None
    try:
        async with s.context.expect_page(timeout=8000) as pinfo:
            await apply_btn.click()
        new_page = await pinfo.value
        await new_page.wait_for_load_state("domcontentloaded")
    except Exception:  # noqa: BLE001 — same-tab navigation
        new_page = page
    await human_pause(1.5, 3.0)

    target = new_page
    url = target.url.lower()

    # Google Form?
    if "docs.google.com/forms" in url:
        gres = await forms.submit_google_form(target, profile, answerer)
        app.method = "google_form"
        _apply_form_result(app, gres, await _screenshot(target, job) if gres.submitted else None)
        return app

    # Generic external site: fill what we can, then look for a submit.
    resume_path = profile.identity.resume_path
    fres = await forms.fill_generic_form(target, profile, answerer, resume_path)
    app.answers = fres.answers
    if fres.needs_input:
        app.status = JobStatus.needs_input
        app.needs_input_prompt = (
            (fres.prompt or "External application needs manual completion.")
            + f" Site: {target.url}"
        )
        return app

    submit = await target.query_selector(
        "button[type='submit'], button:has-text('Submit'), button:has-text('Apply')"
    )
    if submit:
        await submit.click()
        await human_pause(1.0, 2.0)
        app.status = JobStatus.applied
        app.submitted_at = _now()
        app.screenshot_path = await _screenshot(target, job)
    else:
        app.status = JobStatus.needs_input
        app.needs_input_prompt = (
            f"Filled the form but couldn't find a submit button at {target.url}."
        )
    return app


def _apply_form_result(app: Application, res: forms.FormResult, shot: str | None) -> None:
    app.answers = res.answers
    if res.submitted:
        app.status = JobStatus.applied
        app.submitted_at = _now()
        app.screenshot_path = shot
    elif res.needs_input:
        app.status = JobStatus.needs_input
        app.needs_input_prompt = res.prompt
    else:
        app.status = JobStatus.failed
        app.error = res.error
