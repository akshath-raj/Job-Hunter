"""OpenAI calls must use max_completion_tokens (gpt-5/o-series), fall back to
max_tokens only when the model rejects the new parameter."""

from __future__ import annotations

import sys
import types

import pytest

from job_hunter import llm


class _Resp:
    def __init__(self, text):
        msg = types.SimpleNamespace(content=text)
        self.choices = [types.SimpleNamespace(message=msg)]


def _install_fake_openai(monkeypatch, behavior):
    """behavior(kwargs) -> text, or raises. Records calls."""
    calls = []

    class _Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return behavior(kwargs)

    class _Chat:
        completions = _Completions()

    class _Client:
        def __init__(self, api_key=None):
            self.chat = _Chat()

    fake = types.ModuleType("openai")
    fake.OpenAI = _Client
    monkeypatch.setitem(sys.modules, "openai", fake)
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    return calls


def test_uses_max_completion_tokens(monkeypatch):
    calls = _install_fake_openai(monkeypatch, lambda kw: _Resp("ok"))
    out = llm._openai_complete("gpt-5-mini", "sys", "user", 500)
    assert out == "ok"
    assert calls[0]["max_completion_tokens"] == 500
    assert "max_tokens" not in calls[0]


def test_falls_back_to_max_tokens(monkeypatch):
    def behavior(kw):
        if "max_completion_tokens" in kw:
            raise ValueError("Unsupported parameter: 'max_completion_tokens'")
        return _Resp("legacy")

    calls = _install_fake_openai(monkeypatch, behavior)
    out = llm._openai_complete("some-old-model", "sys", "user", 300)
    assert out == "legacy"
    assert len(calls) == 2                      # tried new param, then fell back
    assert calls[1]["max_tokens"] == 300


def test_unrelated_error_is_reraised(monkeypatch):
    def behavior(kw):
        raise RuntimeError("network down")

    _install_fake_openai(monkeypatch, behavior)
    with pytest.raises(RuntimeError, match="network down"):
        llm._openai_complete("gpt-5-mini", "sys", "user", 100)
