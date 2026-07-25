"""The eligibility gate — the safety-critical logic. Well covered on purpose."""

from __future__ import annotations

import pytest

from job_hunter import constraints
from job_hunter.models import Profile, Seniority
from job_hunter.resume import analyze


@pytest.fixture
def student() -> Profile:
    p = Profile()
    analyze.apply_analysis(p, {
        "target_roles": ["Backend Engineer"],
        "seniority": "intern",
        "is_student": True,
        "graduation_date": "2027-05-01",
        "years_experience": 0.5,
    })
    return p


def test_student_seniority_is_capped_to_entry(student):
    assert student.constraints.max_seniority == Seniority.entry


@pytest.mark.parametrize("title,eligible", [
    ("Backend Engineer Intern", True),
    ("Junior Backend Engineer", True),
    ("Software Engineer II", False),      # mid
    ("Senior Backend Engineer", False),
    ("Staff Software Engineer", False),
    ("Engineering Manager", False),
])
def test_student_cannot_apply_above_entry(student, make_job, title, eligible):
    ok, _ = constraints.check(make_job(title), student)
    assert ok is eligible


def test_excluded_company_blocks(student, make_job):
    student.constraints.exclude_companies = ["EvilCorp"]
    ok, reason = constraints.check(make_job("Backend Engineer Intern", company="EvilCorp"), student)
    assert not ok and "excluded company" in reason


def test_excluded_keyword_blocks(student, make_job):
    # Deal-breakers match the TITLE/company, not the JD body (avoids false
    # positives like "Software Engineer" appearing in an ML posting's text).
    student.constraints.exclude_keywords = ["crypto"]
    job = make_job("Crypto Trading Engineer", description="Great team.")
    ok, reason = constraints.check(job, student)
    assert not ok and "deal-breaker" in reason


def test_excluded_keyword_ignores_jd_body(student, make_job):
    student.constraints.exclude_keywords = ["Software Engineer"]
    job = make_job("Machine Learning Intern", description="Work with software engineers.")
    ok, _ = constraints.check(job, student)
    assert ok            # ML Intern is NOT excluded just because the JD mentions SWEs


def test_remote_only_blocks_onsite(student, make_job):
    student.constraints.remote_only = True
    job = make_job("Backend Engineer Intern", location="New York, NY", workplace_type="On-site")
    ok, reason = constraints.check(job, student)
    assert not ok and "remote" in reason


def test_experience_gate(student, make_job):
    job = make_job("Backend Engineer", description="You should have 8+ years of experience.")
    ok, reason = constraints.check(job, student)
    assert not ok and "experience" in reason


def test_infer_seniority_prefers_most_senior_signal(make_job):
    assert constraints.infer_seniority(make_job("Senior Staff Engineer")) == Seniority.staff
