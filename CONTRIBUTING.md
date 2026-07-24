# Contributing to Job Hunter

Thanks for your interest! This project automates a real, messy surface (LinkedIn
+ arbitrary career sites), so contributions that make it more robust are very
welcome.

## Development setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium
```

## Running checks

```bash
pytest           # unit tests (no network, browser, or API keys required)
ruff check .     # lint
```

The test suite intentionally avoids network, browser, and LLM calls so it runs
fast and offline in CI. Logic that touches those (LinkedIn DOM, provider APIs) is
isolated behind seams so it can be unit-tested with fakes.

## Project layout

```
src/job_hunter/
  config.py        paths + LLM provider selection
  models.py        Pydantic models (Profile, Constraints, Job, Application)
  store.py         SQLite persistence
  constraints.py   eligibility gate (the student-vs-senior safety rules)
  llm.py           provider-agnostic LLM (Anthropic / OpenAI)
  resume/          extract text + analyze into a role profile
  linkedin/        persistent browser, search/scrape, Easy Apply
  apply/           answerer, generic/Google forms, Gmail codes, engine
  service.py       high-level ops shared by CLI + MCP
  cli.py           Typer CLI
  mcp_server.py    FastMCP server (Claude Code integration)
```

## Guidelines

- **Selectors change.** When LinkedIn/site markup breaks a scraper, prefer adding
  fallback selectors over replacing existing ones.
- **Never guess on a real submission.** If a required field can't be answered
  confidently, return `needs_input` and let the user decide.
- **Respect the eligibility gate.** Nothing should submit a job that fails
  `constraints.check`. Add tests when you touch that logic.
- Keep new code style-consistent with what's around it; run `ruff` before pushing.

## Reporting issues

Include the command you ran, the job URL/type if relevant, and the full error.
Please don't paste real credentials or verification codes.

## A note on scope & ethics

Automating applications can violate site Terms of Service and can submit real
applications under a real identity. Keep contributions oriented toward *helping a
person apply to their own jobs responsibly* — human-in-the-loop fallbacks,
rate-limiting, and honesty over reach.
