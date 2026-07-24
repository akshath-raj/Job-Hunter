<h1 align="center">🎯 Job Hunter</h1>

<p align="center">
  <em>An autonomous LinkedIn job-hunting agent — as a CLI and as an MCP server for Claude Code.</em>
</p>

<p align="center">
  <a href="#quickstart">Quickstart</a> ·
  <a href="#two-ways-to-run-it">Two ways to run it</a> ·
  <a href="#how-it-works">How it works</a> ·
  <a href="#configuration">Configuration</a> ·
  <a href="#safety--limitations">Safety</a>
</p>

---

Point it at your resume. It figures out what roles fit you, searches LinkedIn,
and applies on your behalf — while respecting hard rules you set (e.g. *a college
student never applies to senior roles*). It fills LinkedIn Easy Apply, external
career sites, and Google Forms; when a site needs an account it can read the
email verification code from Gmail; and when it truly can't answer something, it
pauses and asks you.

> [!WARNING]
> **Read [Safety & limitations](#safety--limitations) before using.** Automating
> LinkedIn violates its Terms of Service and can get your account restricted. In
> autonomous mode this submits **real applications under your real identity**.
> Use it deliberately, on your own accounts, and start with a small `--limit`.

## Quickstart

```bash
git clone https://github.com/akshath/job-hunter && cd job-hunter
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
playwright install chromium          # or rely on your system Chrome

job-hunter login                                       # sign into LinkedIn once
job-hunter onboard -r ~/resume.pdf -d "remote backend roles, no crypto"
job-hunter search                                      # find + store matches
job-hunter apply --limit 5                             # autonomously apply
```

## Two ways to run it

| | **Standalone CLI** | **MCP server (Claude Code)** |
|---|---|---|
| Who's the "brain"? | An LLM you configure (Anthropic **or** OpenAI) | Claude Code itself |
| API key needed? | Yes | **No** |
| Novel / weird sites | Best-effort heuristics | Claude drives Playwright directly |
| Best for | Batch, unattended runs | Interactive, hard cases, oversight |

### Standalone CLI

Set a provider key in `.env` (see [Configuration](#configuration)), then:

```bash
job-hunter run -r ~/resume.pdf -d "new-grad SWE, US only" --limit 5   # whole pipeline
# or step by step:
job-hunter onboard -r resume.pdf -d "…"   # analyze resume + ask 10th/12th marks etc. (once)
job-hunter search                          # search LinkedIn, store jobs
job-hunter enrich                          # research company/salary/quals (cheap subagents)
job-hunter export                          # write jobs.xlsx to review
job-hunter jobs --status eligible          # review what passed your rules
job-hunter apply --limit 5                 # AUTO: apply autonomously to top matches
job-hunter apply --mode select             # HUMAN-IN-LOOP: list jobs, you pick which
job-hunter apply --concurrency 3           # apply to several at once (bounded)
```

**Two apply modes:** `--mode auto` applies to your top eligible matches with no
prompts; `--mode select` lists the enriched jobs and lets you choose exactly
which to apply to. Anything the resume didn't cover (10th/12th marks, CGPA,
notice period) is asked **once** at onboarding and remembered across all future
sessions — and reused to auto-answer application questions.

### As an MCP server for Claude Code

Register the server (no API key required — Claude Code is the brain):

```bash
claude mcp add job-hunter -- job-hunter-mcp
```

Then just talk to Claude Code:

> *"Onboard me from ~/resume.pdf. I'm a college student looking for summer
> internships, remote only. Find matching jobs and apply to 5."*

Claude calls the tools — `analyze_resume_prompt` → `save_resume_analysis` →
`set_constraints` → `login_linkedin` → `search_jobs` → `apply_batch` — asks you
for anything missing (phone, work authorization…), and for unusual application
forms it drives the [Playwright MCP](https://github.com/microsoft/playwright-mcp)
directly with full page context.

<details>
<summary><strong>MCP tools reference</strong></summary>

| Tool | Purpose |
|------|---------|
| `analyze_resume_prompt` / `save_resume_analysis` | Understand the resume (Claude reasons it out — no key needed) |
| `get_profile` / `set_profile_fields` / `missing_profile_fields` | Manage identity details |
| `missing_extra_fields` / `remember_answers` / `get_extra` | Ask-once memory (10th/12th marks, CGPA…) reused every session |
| `set_constraints` | Hard rules — student, seniority ceiling, remote, locations, exclusions |
| `login_linkedin` | One-time LinkedIn sign-in |
| `search_jobs` / `list_jobs` / `job_status_counts` | Find and inspect jobs |
| `enrichment_tasks` / `set_job_enrichment` | Research company/salary/quals (run as cheap subagents w/ web search) |
| `export_excel` | Write a spreadsheet of jobs for the human-in-the-loop pick |
| `apply_batch` (mode=`auto`\|`select`) / `apply_to_job` | Autonomous or human-selected applying (eligibility-gated, concurrent) |
| `pending_input_jobs` | What's paused waiting on you |

**Recommended MCP flow for "apply to N jobs":** `analyze_resume_prompt` →
`save_resume_analysis` → `missing_extra_fields` (ask user, `remember_answers`) →
`search_jobs` → `enrichment_tasks` (spawn **Haiku** subagents w/ WebSearch for
salary) → `set_job_enrichment` → `export_excel`. Then **autonomous:**
`apply_batch(mode="auto")`, or **human-in-the-loop:** show the sheet, ask which,
`apply_batch(mode="select", selected_ids=[…])`.

</details>

## How it works

```
resume ─▶ extract ─▶ role-analyzer ─▶ profile.json ◀─ onboarding (name, phone, work auth…)
                                          │
                                   constraints engine   ← the "student ≠ senior" safety gate
                                          │
LinkedIn (your session) ─▶ search/scrape ─▶ jobs.db ─▶ application engine
                                                             ├─ Easy Apply
                                                             ├─ Google Form
                                                             └─ external site (+ signup via Gmail codes)
```

- **Role analyzer** turns your resume into target roles, seniority, and skills —
  and *infers hard rules* (a current student is capped at entry-level).
- **Constraints engine** is the safety gate: every job must satisfy **all** your
  rules or it's skipped. Nothing that fails it is ever submitted.
- **Application engine** answers questions from your profile first (free, exact),
  falls back to the LLM for free-text, uploads your resume, and **pauses rather
  than guessing** on required fields it can't answer confidently.
- All state lives in `~/.jobhunter/` (profile, job DB, persistent browser
  session, submission screenshots). Runs are resumable and de-duplicated.

## Configuration

Copy `.env.example` → `.env`. Everything is optional except a provider key for
standalone runs.

| Variable | Purpose |
|----------|---------|
| `ANTHROPIC_API_KEY` | Use Anthropic as the standalone brain |
| `OPENAI_API_KEY` | Use OpenAI as the standalone brain |
| `JOBHUNTER_PROVIDER` | Force `anthropic` or `openai` (else: whichever key is set) |
| `JOBHUNTER_MODEL` | Override the default model |
| `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` | Auto-read signup verification codes ([App Password](https://myaccount.google.com/apppasswords), never your real password) |
| `JOBHUNTER_HOME` | Where local state lives (default `~/.jobhunter`) |

**Provider selection:** if you set both keys, Anthropic wins unless
`JOBHUNTER_PROVIDER=openai`. Default models: `claude-sonnet-4-6` /
`gpt-4o` (override with `JOBHUNTER_MODEL`).

## Reusing your existing Chrome login

By default the tool runs Chrome with an **isolated profile**
(`~/.jobhunter/chrome-profile`) so it never conflicts with your everyday browser
— you log into LinkedIn once. Chrome locks a profile to a single process, so it
can't share your live default profile while your normal Chrome is open.

To reuse your **already-logged-in** session, attach over CDP:

```bash
# 1. Quit Chrome completely, then relaunch with a debugging port (macOS):
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --remote-debugging-port=9222
# 2. Point the tool at it:
export JOBHUNTER_CDP_URL=http://localhost:9222
```

Now `job-hunter` drives your real browser/profile and **won't close it** on exit.
Alternatively set `JOBHUNTER_CHROME_USER_DATA_DIR` + `JOBHUNTER_CHROME_PROFILE`
to launch your real profile directly — but Chrome must be fully closed while it
runs.

## Development

```bash
pip install -e ".[dev]"
pytest          # fast, offline — no network/browser/API keys
ruff check .
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the project layout and guidelines.

## Safety & limitations

- **Terms of Service.** LinkedIn's User Agreement prohibits automated scraping
  and applying. This tool drives *your own* logged-in Chrome at a human pace to
  reduce risk, but using it can still get your account restricted or banned.
- **Real submissions.** Autonomous mode submits real applications under your
  identity. Review your constraints, start with `--limit 1`, and check the
  `~/.jobhunter/artifacts/` screenshots.
- **Easy Apply is the reliable path.** External sites and Google Forms are
  best-effort; anything the tool can't complete confidently is marked
  `needs_input` for you to finish — it never fabricates answers on a real form.
- **Selectors drift.** LinkedIn's markup changes often; scrapers use defensive
  fallbacks but may occasionally need updates (PRs welcome).

## License

[MIT](LICENSE) © 2026 Akshath
