"""Search variations across all locations + work-style acceptance."""

from __future__ import annotations

from job_hunter import constraints
from job_hunter import profile as profile_mod
from job_hunter.models import Profile
from job_hunter.service import _search_tasks

# ---- multi-location variations (the "only Bangalore" bug) -----------------

def test_search_covers_all_cities_and_remote():
    p = Profile()
    p.search_keywords = ["ML Engineer"]
    p.constraints.locations = ["Bengaluru", "Chennai", "Remote"]
    tasks = _search_tasks(p, p.search_keywords, recent_days=None)
    cities = {t["location"] for t in tasks if t["location"]}
    assert "Bengaluru" in cities and "Chennai" in cities   # not just the first city
    assert any(t["remote"] for t in tasks)                 # a remote pass exists
    assert any(t["sort"] == "recent" for t in tasks)       # a newly-posted pass exists


def test_variations_bounded_by_keyword_cap():
    p = Profile()
    p.search_keywords = [f"kw{i}" for i in range(20)]
    p.constraints.locations = ["Bengaluru"]
    tasks = _search_tasks(p, p.search_keywords, recent_days=None)
    assert len({t["kw"] for t in tasks}) <= 6              # keyword cap applied


# ---- work-style parsing + acceptance --------------------------------------

def test_parse_work_styles():
    assert profile_mod.parse_work_styles("remote, hybrid") == ["remote", "hybrid"]
    assert profile_mod.parse_work_styles("any") == []
    assert profile_mod.parse_work_styles("WFH / On-site") == ["remote", "onsite"]


def test_hybrid_accepted_when_listed(make_job):
    p = Profile()
    p.constraints.locations = ["Bengaluru"]
    p.constraints.workplace_types = ["remote", "hybrid"]
    job = make_job("ML Engineer")
    job.location = "Bengaluru, Karnataka, India"
    job.workplace_type = "Hybrid"
    assert constraints.check(job, p)[0]


def test_onsite_rejected_when_not_listed(make_job):
    p = Profile()
    p.constraints.workplace_types = ["remote", "hybrid"]
    job = make_job("ML Engineer")
    job.location = "Bengaluru"
    job.workplace_type = "On-site"
    ok, reason = constraints.check(job, p)
    assert not ok and "work style" in reason


def test_any_workstyle_accepts_all(make_job):
    p = Profile()
    p.constraints.locations = ["Bengaluru"]
    p.constraints.workplace_types = []          # any
    job = make_job("ML Engineer")
    job.location = "Bengaluru"
    job.workplace_type = "On-site"
    assert constraints.check(job, p)[0]
