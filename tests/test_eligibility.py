"""Final eligibility agent — RAG classification (no LLM, deterministic paths)."""

from __future__ import annotations

from job_hunter import eligibility
from job_hunter import profile as profile_mod
from job_hunter.models import Profile, Seniority


def _student() -> Profile:
    p = Profile()
    p.constraints.is_student = True
    p.constraints.max_seniority = Seniority.entry
    p.constraints.locations = ["Bengaluru", "Remote"]
    p.constraints.workplace_types = ["remote", "hybrid"]
    return p


def test_green_when_everything_fits(make_job):
    job = make_job("Machine Learning Intern")
    job.location = "Bengaluru, Karnataka, India"
    job.workplace_type = "Hybrid"
    rag, flags = eligibility.classify(job, _student(), use_llm=False)
    assert rag == "green"
    assert flags == []


def test_red_on_location(make_job):
    job = make_job("Machine Learning Intern")
    job.location = "Berlin, Germany"
    job.workplace_type = "On-site"
    rag, flags = eligibility.classify(job, _student(), use_llm=False)
    assert rag == "red"
    assert any("location" in f for f in flags)


def test_red_on_seniority(make_job):
    job = make_job("Senior Machine Learning Engineer")
    job.location = "Bengaluru"
    job.workplace_type = "Remote"
    rag, flags = eligibility.classify(job, _student(), use_llm=False)
    assert rag == "red"
    assert any("level" in f for f in flags)


def test_red_on_dealbreaker(make_job):
    p = _student()
    p.constraints.exclude_keywords = ["crypto"]
    job = make_job("ML Intern")
    job.location = "Bengaluru"
    job.workplace_type = "Remote"
    job.description = "Join our crypto trading team."
    rag, flags = eligibility.classify(job, p, use_llm=False)
    assert rag == "red"
    assert any("crypto" in f for f in flags)


def test_yellow_on_salary_below_expectation(make_job):
    p = _student()
    profile_mod.remember(p, "expected salary", "20 LPA")
    job = make_job("Machine Learning Intern")
    job.location = "Bengaluru"
    job.workplace_type = "Remote"
    job.salary = "avg ~₹8 LPA (INR)"
    rag, flags = eligibility.classify(job, p, use_llm=False)
    assert rag == "yellow"                      # eligible, but salary is a soft concern
    assert any("below your expectation" in f for f in flags)
