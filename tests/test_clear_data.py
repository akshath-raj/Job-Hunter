"""Deleting stored user data — full wipe and selective scopes."""

from __future__ import annotations

from job_hunter import config, service, store
from job_hunter import profile as profile_mod
from job_hunter.models import JobStatus, Profile


def _seed(make_job):
    p = Profile()
    p.identity.full_name = "Jane Doe"
    profile_mod.save(p)
    config.RESUME_TEXT_PATH.write_text("resume text")
    j = make_job("Backend Engineer")
    j.status = JobStatus.eligible
    store.upsert_job(j)


def test_clear_all_removes_profile_and_jobs(make_job):
    _seed(make_job)
    assert config.PROFILE_PATH.exists()
    assert store.list_jobs()

    res = service.clear_data()
    assert res["count"] >= 1
    assert not config.PROFILE_PATH.exists()
    assert not config.RESUME_TEXT_PATH.exists()
    assert store.list_jobs() == []          # db recreated empty on next access


def test_selective_keeps_jobs(make_job):
    _seed(make_job)
    service.clear_data(profile=True, jobs=False, session=False,
                       artifacts=False, spreadsheet=False)
    assert not config.PROFILE_PATH.exists()   # profile gone
    assert store.list_jobs()                  # jobs kept


def test_keep_login_preserves_session_dir(make_job):
    config.BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    (config.BROWSER_PROFILE_DIR / "Cookies").write_text("x")
    service.clear_data(session=False)
    assert config.BROWSER_PROFILE_DIR.exists()
