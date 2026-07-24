"""Requirement cross-check: flag jobs that don't suit the user's conditions."""

from __future__ import annotations

from job_hunter import fit
from job_hunter import profile as profile_mod
from job_hunter.models import Profile


def _profile_with(expected=None, exclude=None, workplace=None) -> Profile:
    p = Profile()
    if expected:
        profile_mod.remember(p, "expected salary", expected)
    if exclude:
        p.constraints.exclude_keywords = exclude
    if workplace:
        p.constraints.workplace_types = workplace
    return p


def test_flags_salary_below_expectation_lpa(make_job):
    p = _profile_with(expected="20 LPA")
    job = make_job("ML Engineer")
    job.salary = "₹8-12 LPA (INR)"
    flags = fit.check(job, p)
    assert any("below your expectation" in f for f in flags)


def test_no_flag_when_salary_meets_expectation(make_job):
    p = _profile_with(expected="20 LPA")
    job = make_job("ML Engineer")
    job.salary = "₹22-30 LPA (INR)"
    assert not any("below" in f for f in fit.check(job, p))


def test_usd_salary_comparison(make_job):
    p = _profile_with(expected="$150k")
    job = make_job("ML Engineer")
    job.salary = "$90k-$110k/yr (USD)"
    assert any("below your expectation" in f for f in fit.check(job, p))


def test_flags_dealbreaker_keyword(make_job):
    p = _profile_with(exclude=["clearance"])
    job = make_job("ML Engineer")
    job.description = "Requires an active security clearance."
    assert any("deal-breaker" in f for f in fit.check(job, p))


def test_flags_high_competition(make_job):
    job = make_job("ML Engineer")
    job.num_applicants = "Over 200 applicants"
    assert any("competitive" in f for f in fit.check(job, Profile()))


def test_no_cross_currency_false_positive(make_job):
    # Expected in LPA, salary in USD -> we must NOT compare/flag.
    p = _profile_with(expected="20 LPA")
    job = make_job("ML Engineer")
    job.salary = "$5k (USD)"
    assert not any("below" in f for f in fit.check(job, p))


def test_merge_combines_and_dedupes_none():
    assert fit.merge([], "none") is None
    assert fit.merge(["a"], "") == "a"
    assert fit.merge(["a"], "b") == "a; b"
