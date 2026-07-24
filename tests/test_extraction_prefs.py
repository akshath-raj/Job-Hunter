"""Resume detail extraction into memory + one-time search preferences."""

from __future__ import annotations

from job_hunter import profile as profile_mod
from job_hunter.models import Profile
from job_hunter.resume import analyze


def test_extracted_details_land_in_memory():
    p = Profile()
    analyze.apply_analysis(p, {
        "target_roles": ["ML Engineer"],
        "extracted_details": {
            "10th grade percentage": "96%",
            "12th grade percentage": "93%",
            "undergraduate cgpa": "9.35",
            "university": "VIT",
        },
    })
    assert profile_mod.recall(p, "What was your 10th grade percentage?") == "96%"
    assert profile_mod.recall(p, "undergraduate CGPA") == "9.35"
    # And so we never re-ask for them:
    assert profile_mod.missing_extra_fields(p).get("10th grade percentage or GPA") is None


def test_no_details_when_absent():
    p = Profile()
    analyze.apply_analysis(p, {"target_roles": ["ML Engineer"], "extracted_details": {}})
    assert p.extra == {}


def test_search_prefs_collected_once():
    p = Profile()
    assert profile_mod.needs_search_preferences(p) is True
    profile_mod.set_search_preferences(p, expected_salary="20 LPA",
                                       locations="Bangalore, Remote", remote_only=False)
    assert profile_mod.needs_search_preferences(p) is False
    assert profile_mod.recall(p, "expected salary") == "20 LPA"
    assert p.constraints.locations == ["Bangalore", "Remote"]
