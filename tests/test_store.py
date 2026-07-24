"""Persistence: dedup on upsert, status filtering, application round-trips."""

from __future__ import annotations

from job_hunter import store
from job_hunter.models import Application, JobStatus


def test_upsert_dedups(make_job):
    job = make_job("Backend Engineer")
    assert store.upsert_job(job) is True     # new
    assert store.upsert_job(job) is False    # already present


def test_list_by_status(make_job):
    a = make_job("A")
    a.status = JobStatus.eligible
    b = make_job("B")
    b.status = JobStatus.ineligible
    store.upsert_job(a)
    store.upsert_job(b)
    eligible = store.list_jobs(status=JobStatus.eligible)
    assert [j.title for j in eligible] == ["A"]


def test_application_roundtrip():
    app = Application(job_id="xyz", status=JobStatus.applied, method="easy_apply",
                      answers={"Email": "a@b.com"})
    store.save_application(app)
    got = store.get_application("xyz")
    assert got is not None
    assert got.status == JobStatus.applied
    assert got.answers["Email"] == "a@b.com"


def test_counts_by_status(make_job):
    a = make_job("A")
    a.status = JobStatus.eligible
    store.upsert_job(a)
    counts = store.counts_by_status()
    assert counts.get("eligible") == 1
