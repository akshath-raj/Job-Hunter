"""Ask-once persistent memory: remember, recall (fuzzy), and answerer reuse."""

from __future__ import annotations

from job_hunter import profile as profile_mod
from job_hunter.apply.answerer import ProfileAnswerer
from job_hunter.models import Profile


def test_remember_and_recall_exact():
    p = Profile()
    profile_mod.remember(p, "What was your 10th grade percentage?", "92%")
    assert profile_mod.recall(p, "What was your 10th grade percentage?") == "92%"


def test_recall_is_fuzzy():
    p = Profile()
    profile_mod.remember(p, "10th grade percentage or GPA", "92%")
    # A differently-phrased application question should still hit.
    assert profile_mod.recall(p, "Please enter your 10th grade percentage") == "92%"


def test_recall_miss_returns_none():
    assert profile_mod.recall(Profile(), "favorite color") is None


def test_missing_extra_shrinks_after_answering():
    p = Profile()
    before = profile_mod.missing_extra_fields(p)
    assert "notice period" in before
    profile_mod.apply_extra(p, {"What is your notice period?": "2 weeks"})
    after = profile_mod.missing_extra_fields(p)
    assert len(after) < len(before)


async def test_answerer_uses_memory_before_llm():
    p = Profile()
    profile_mod.remember(p, "12th grade percentage or GPA", "88%")
    a = ProfileAnswerer(p, use_llm=False)  # no LLM -> must come from memory
    assert await a("What was your 12th grade percentage?", None) == "88%"
