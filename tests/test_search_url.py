"""The LinkedIn search URL only restricts to Easy Apply when explicitly asked."""

from __future__ import annotations

from job_hunter.linkedin import search


def test_broad_search_has_no_easy_apply_filter():
    url = search.build_url("Machine Learning Engineer", easy_apply=False)
    assert "f_AL" not in url            # not restricted to Easy Apply
    assert "keywords=Machine" in url


def test_easy_apply_only_adds_filter():
    url = search.build_url("Machine Learning Engineer", easy_apply=True)
    assert "f_AL=true" in url


def test_remote_and_location_params():
    url = search.build_url("SWE", location="Bangalore", easy_apply=False, remote=True)
    assert "location=Bangalore" in url
    assert "f_WT=2" in url              # remote workplace filter
