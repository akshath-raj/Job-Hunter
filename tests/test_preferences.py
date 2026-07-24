"""LLM-processed search preferences: merge logic + service orchestration."""

from __future__ import annotations

from job_hunter import config, preferences, service
from job_hunter import profile as profile_mod
from job_hunter.models import Profile


def test_apply_processed_merges_everything():
    p = Profile()
    p.search_keywords = ["Old Keyword"]
    preferences.apply_processed(p, {
        "expected_salary": "20 LPA",
        "locations": ["Bangalore", "Remote"],
        "remote_only": False,
        "refined_keywords": ["ML Engineer", "Computer Vision Intern"],
        "exclude_keywords": ["unpaid", "clearance"],
        "search_context": "Prioritize ML/CV roles at product companies.",
    })
    # Résumé-derived keywords stay primary; refined ones are appended.
    assert p.search_keywords[0] == "Old Keyword"
    assert "ML Engineer" in p.search_keywords
    assert "Computer Vision Intern" in p.search_keywords
    assert p.constraints.locations == ["Bangalore", "Remote"]
    assert "unpaid" in p.constraints.exclude_keywords
    assert p.search_context.startswith("Prioritize ML/CV")
    assert profile_mod.recall(p, "expected salary") == "20 LPA"
    assert p.search_prefs_collected is True


def test_process_uses_llm_when_available(monkeypatch):
    monkeypatch.setattr(config, "has_llm", lambda: True)
    monkeypatch.setattr(
        preferences, "process",
        lambda brief, roles, raw: {"refined_keywords": ["ML Engineer"],
                                   "search_context": "ctx from additional details"},
    )
    profile_mod.save(Profile())
    res = service.process_search_preferences(
        {"salary": "", "locations": "", "remote": "", "additional": "prefer product companies"}
    )
    assert res["processed"] is True
    assert "ML Engineer" in res["search_keywords"]
    assert profile_mod.load().search_context == "ctx from additional details"


def test_process_falls_back_without_llm(monkeypatch):
    monkeypatch.setattr(config, "has_llm", lambda: False)
    profile_mod.save(Profile())
    res = service.process_search_preferences(
        {"salary": "20 LPA", "locations": "Bangalore", "remote": "yes", "additional": "no crypto"}
    )
    assert res["processed"] is False
    p = profile_mod.load()
    assert p.search_context == "no crypto"          # raw additional details kept
    assert p.constraints.remote_only is True
    assert p.constraints.locations == ["Bangalore"]
