"""Shared fixtures. Points all state at a throwaway temp dir per test."""

from __future__ import annotations

import pytest

from job_hunter import config
from job_hunter.models import Job


@pytest.fixture(autouse=True)
def tmp_home(tmp_path, monkeypatch):
    """Redirect config paths to a temp dir so tests never touch real state."""
    monkeypatch.setattr(config, "HOME", tmp_path)
    monkeypatch.setattr(config, "PROFILE_PATH", tmp_path / "profile.json")
    monkeypatch.setattr(config, "RESUME_TEXT_PATH", tmp_path / "resume.txt")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "jobs.db")
    monkeypatch.setattr(config, "BROWSER_PROFILE_DIR", tmp_path / "chrome")
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(config, "ARTIFACTS_DIR", tmp_path / "artifacts")
    return tmp_path


@pytest.fixture
def make_job():
    """Factory for Job instances in tests."""

    def _make(title: str, company: str = "Acme", **kw) -> Job:
        return Job(
            id=title.replace(" ", "")[:12],
            source="linkedin",
            external_id=title,
            url="https://example.com/job",
            title=title,
            company=company,
            **kw,
        )

    return _make
