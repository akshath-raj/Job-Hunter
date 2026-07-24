"""Diagnostics snapshot + SerpAPI web-research backend selection."""

from __future__ import annotations

from job_hunter import enrich, service, store
from job_hunter import profile as profile_mod
from job_hunter.models import JobStatus, Profile


def test_diagnostics_reports_state(make_job, monkeypatch):
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    p = Profile()
    p.target_roles = ["ML Engineer"]
    p.search_keywords = ["ML Engineer"]
    profile_mod.save(p)
    good = make_job("ML Engineer")
    good.status = JobStatus.eligible
    good.enriched = True
    good.salary = "₹20 LPA"
    store.upsert_job(good)

    d = service.diagnostics()
    assert d["llm"]["key_present"] is True
    assert d["web_research"] == "browser scraping"
    assert d["jobs"]["total"] == 1
    assert d["jobs"]["enriched"] == 1
    assert d["jobs"]["with_salary"] == 1
    assert d["profile"]["exists"] is True
    assert d["profile"]["search_keywords"] == ["ML Engineer"]


def test_web_research_reports_serpapi(monkeypatch):
    monkeypatch.setenv("SERPAPI_KEY", "serp-123")
    assert service.diagnostics()["web_research"] == "SerpAPI"


async def test_serpapi_snippets_empty_without_key(monkeypatch):
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    assert await enrich._serpapi_snippets("anything") == ""


async def test_search_snippets_prefers_serpapi(monkeypatch):
    async def fake_serp(query):
        return "SerpAPI result: salary $150k at Acme"

    monkeypatch.setattr(enrich, "_serpapi_snippets", fake_serp)
    # If SerpAPI returns text, the browser engines are never touched (context=None ok).
    out = await enrich._search_snippets(None, "acme salary")
    assert "SerpAPI result" in out
