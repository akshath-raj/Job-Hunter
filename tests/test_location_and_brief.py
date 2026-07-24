"""Location aliases, keyword preservation, brief append-not-overwrite, sheet filter."""

from __future__ import annotations

from job_hunter import constraints, preferences, service, store
from job_hunter.models import JobStatus, Profile
from job_hunter.resume import analyze

# ---- location aliases -----------------------------------------------------

def test_bangalore_matches_bengaluru(make_job):
    p = Profile()
    p.constraints.locations = ["Bangalore", "Remote"]
    job = make_job("ML Engineer")
    job.location = "Bengaluru, Karnataka, India"
    job.workplace_type = "Hybrid"
    ok, reason = constraints.check(job, p)
    assert ok, reason


def test_unrelated_city_still_rejected(make_job):
    p = Profile()
    p.constraints.locations = ["Bangalore"]
    job = make_job("ML Engineer")
    job.location = "Berlin, Germany"
    ok, reason = constraints.check(job, p)
    assert not ok and "location" in reason


def test_remote_allowed_when_listed(make_job):
    p = Profile()
    p.constraints.locations = ["Bangalore", "Remote"]
    job = make_job("ML Engineer")
    job.location = "India"
    job.workplace_type = "Remote"
    assert constraints.check(job, p)[0]


# ---- keywords are preserved, not replaced ---------------------------------

def test_resume_keywords_stay_primary():
    p = Profile()
    p.search_keywords = ["Machine Learning Engineer", "Data Science Intern"]
    preferences.apply_processed(p, {"refined_keywords": ["AI Research Intern"]})
    assert p.search_keywords[0] == "Machine Learning Engineer"   # résumé keyword still first
    assert "AI Research Intern" in p.search_keywords             # refined appended


# ---- brief: search prefs appended, résumé part untouched ------------------

def test_brief_append_preserves_resume(tmp_home):
    analyze.persist_brief({"brief": "## Who they are\nML engineer."})
    analyze.update_brief_search_section("Prefer remote ML roles.")
    from job_hunter import config
    text = config.BRIEF_PATH.read_text()
    assert "## Who they are" in text                 # résumé brief kept
    assert "Search preferences" in text
    # Re-running the analysis must keep the search section.
    analyze.persist_brief({"brief": "## Who they are\nML engineer v2."})
    text2 = config.BRIEF_PATH.read_text()
    assert "ML engineer v2" in text2 and "Search preferences" in text2


# ---- excel excludes off-target jobs ---------------------------------------

def test_export_excludes_ineligible(make_job):
    good = make_job("ML Engineer")
    good.status = JobStatus.eligible
    good.match_score = 0.8
    bad = make_job("HR Intern")
    bad.status = JobStatus.ineligible
    store.upsert_job(good)
    store.upsert_job(bad)
    res = service.export_excel()
    assert res["rows"] == 1                            # only the eligible one
