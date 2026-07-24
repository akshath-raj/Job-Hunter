"""The LinkedIn search URL only restricts to Easy Apply when explicitly asked."""

from __future__ import annotations

from job_hunter.linkedin import search


def test_broad_search_has_no_easy_apply_filter():
    url = search.build_url("Machine Learning Engineer", easy_apply=False)
    assert "f_AL" not in url            # not restricted to Easy Apply
    assert "keywords=Machine" in url


def test_default_search_is_all_jobs_by_relevance():
    url = search.build_url("ML Engineer", easy_apply=False)
    assert "sortBy=R" in url            # relevance, not date
    assert "f_TPR" not in url           # no date restriction -> all postings


def test_recent_days_adds_date_filter():
    url = search.build_url("ML Engineer", easy_apply=False, date_posted_days=7, sort="recent")
    assert "f_TPR=r604800" in url       # last 7 days
    assert "sortBy=DD" in url           # most recent first


def test_easy_apply_only_adds_filter():
    url = search.build_url("Machine Learning Engineer", easy_apply=True)
    assert "f_AL=true" in url


def test_remote_and_location_params():
    url = search.build_url("SWE", location="Bangalore", easy_apply=False, remote=True)
    assert "location=Bangalore" in url
    assert "f_WT=2" in url              # remote workplace filter
