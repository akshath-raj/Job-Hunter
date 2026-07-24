"""Provider selection resolves from env correctly (no API calls made)."""

from __future__ import annotations

import pytest

from job_hunter import config


@pytest.fixture(autouse=True)
def clear_env(monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "JOBHUNTER_PROVIDER", "JOBHUNTER_MODEL"):
        monkeypatch.delenv(var, raising=False)


def test_explicit_provider_wins(monkeypatch):
    monkeypatch.setenv("JOBHUNTER_PROVIDER", "openai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert config.llm_provider() == "openai"


def test_anthropic_key_selected(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert config.llm_provider() == "anthropic"


def test_openai_key_selected(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    assert config.llm_provider() == "openai"


def test_anthropic_wins_when_both_present(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("OPENAI_API_KEY", "y")
    assert config.llm_provider() == "anthropic"


def test_default_model_per_provider():
    assert config.model_for("anthropic").startswith("claude")
    assert config.model_for("openai").startswith("gpt")


def test_model_override(monkeypatch):
    monkeypatch.setenv("JOBHUNTER_MODEL", "custom-model")
    assert config.model_for("anthropic") == "custom-model"


def test_has_llm(monkeypatch):
    assert config.has_llm() is False
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    assert config.has_llm() is True
