"""Regression: `run` must pass concrete values to the sub-commands, never Typer
OptionInfo objects (which happens if you call a command fn without all args)."""

from __future__ import annotations

import typer
from typer.testing import CliRunner

from job_hunter import cli


def test_run_passes_concrete_values(monkeypatch):
    captured: dict[str, dict] = {}

    def fake_onboard(resume, description):
        captured["onboard"] = {"resume": resume, "description": description}

    def fake_search(**kw):
        captured["search"] = kw

    def fake_apply(**kw):
        captured["apply"] = kw

    monkeypatch.setattr(cli, "onboard", fake_onboard)
    monkeypatch.setattr(cli, "search", fake_search)
    monkeypatch.setattr(cli, "apply", fake_apply)

    result = CliRunner().invoke(cli.app, ["run", "-r", "resume.pdf", "--tier", "less"])
    assert result.exit_code == 0, result.output

    # No argument passed to a sub-command may be a Typer OptionInfo.
    for cmd in ("search", "apply"):
        for name, value in captured[cmd].items():
            assert not isinstance(value, typer.models.OptionInfo), f"{cmd}.{name} is OptionInfo"

    assert captured["search"]["tier"] == "less"
    assert captured["apply"]["mode"] == "auto"
