"""Salary extraction from postings and web-search research."""

from __future__ import annotations

import pytest

from job_hunter import enrich


@pytest.mark.parametrize("text,expected_substr", [
    ("Base salary is $120,000 - $150,000 per year", "$120,000"),
    ("Compensation: 20 LPA plus benefits", "20 LPA"),
    ("We offer ₹15,00,000 to ₹25,00,000", "₹15,00,000"),
    ("Great team, no pay listed", None),
])
def test_salary_in_text(text, expected_substr):
    got = enrich.salary_in_text(text)
    if expected_substr is None:
        assert got is None
    else:
        assert got is not None and expected_substr in got


async def test_salary_queries_are_role_and_location_based(make_job):
    job = make_job("Machine Learning Engineer")
    job.location = "Bengaluru"
    qs = enrich._salary_queries(job)
    assert all("Acme" not in q for q in qs)          # NOT company-specific
    assert all("Bengaluru" in q for q in qs)         # location included
    assert any("average salary" in q for q in qs)


async def test_jd_salary_takes_priority(monkeypatch, make_job):
    # Salary in the JD must be used; the web must NOT be consulted.
    job = make_job("Backend Engineer")
    job.description = "Role details... Salary: $95,000 - $110,000 a year. Apply now."

    async def boom(context, query):
        raise AssertionError("web search should not run when JD has salary")

    monkeypatch.setattr(enrich, "_search_snippets", boom)
    await enrich.enrich_with_browser(context=None, job=job, use_llm=False)
    assert job.salary and "$95,000" in job.salary
    assert job.enrichment_source == "LinkedIn job description"


def test_llm_json_retry_recovers(monkeypatch):
    calls = {"n": 0}

    def flaky(system, prompt, max_tokens):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("transient parse error")
        return {"about": "ok"}

    monkeypatch.setattr("job_hunter.llm.complete_json", flaky)
    assert enrich._llm_json_retry("s", "p", 100) == {"about": "ok"}
    assert calls["n"] == 2                       # retried once


def test_llm_json_retry_gives_up(monkeypatch):
    def always_fail(system, prompt, max_tokens):
        raise ValueError("nope")

    monkeypatch.setattr("job_hunter.llm.complete_json", always_fail)
    assert enrich._llm_json_retry("s", "p", 100, attempts=2) is None


async def test_enrich_falls_back_to_web(monkeypatch, make_job):
    async def fake_snippets(context, query):
        return "levels.fyi: total comp around $180k"

    monkeypatch.setattr(enrich, "_search_snippets", fake_snippets)
    job = make_job("Staff Engineer")            # no salary in description
    await enrich.enrich_with_browser(context=object(), job=job, use_llm=False)
    assert job.salary and "180" in job.salary
