<div align="center">

# 🎯 Job Hunter

**An autonomous LinkedIn job-hunting agent — as a CLI *and* an MCP server for Claude Code.**

Point it at your résumé. It learns what you do, searches LinkedIn for genuinely
relevant roles, researches the details, and applies on your behalf — while
respecting the hard rules you set.

[![CI](https://github.com/akshath-raj/Job-Hunter/actions/workflows/ci.yml/badge.svg)](https://github.com/akshath-raj/Job-Hunter/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

</div>

> [!WARNING]
> Automating LinkedIn violates its Terms of Service and can get your account
> restricted. In autonomous mode this submits **real applications under your real
> identity**. Use it deliberately, on your own account, and start small. See
> [Safety & limitations](#-safety--limitations).

---

## Table of contents

- [Why](#-why)
- [Features](#-features)
- [How it works](#-how-it-works)
- [Installation](#-installation)
- [Configuration](#-configuration)
- [Usage](#-usage)
  - [Standalone CLI](#standalone-cli)
  - [As an MCP server (Claude Code)](#as-an-mcp-server-claude-code)
- [Command reference](#-command-reference)
- [Reusing your existing Chrome login](#-reusing-your-existing-chrome-login)
- [Your data & privacy](#-your-data--privacy)
- [Safety & limitations](#-safety--limitations)
- [Development](#-development)
- [License](#-license)

---

## 💡 Why

Job hunting is repetitive: read a posting, judge if it fits, fill the same
details again, repeat a hundred times. Job Hunter automates that loop with an
important guardrail — it only applies to roles that pass **your** hard rules
(a student never applies to senior roles) and are actually **relevant to your
background** (no random "Sales Engineer" because it shared a keyword).

It runs two ways from one codebase:

|                     | **Standalone CLI**                     | **MCP server (Claude Code)**            |
| ------------------- | -------------------------------------- | --------------------------------------- |
| Who's the "brain"?  | An LLM you configure (Anthropic/OpenAI) | Claude Code itself                      |
| API key needed?     | Yes                                    | **No**                                  |
| Novel / weird sites | Best-effort heuristics                 | Claude drives the browser directly      |
| Best for            | Unattended batch runs                  | Interactive control & hard cases        |

---

## ✨ Features

- **Résumé understanding** — extracts everything (skills, education marks, CGPA,
  contact) and writes a detailed `candidate_brief.md` describing what you do.
- **Relevant search** — derives precise search keywords for *your* specialization
  and filters out off-target results with a relevance score.
- **Salary research** — when a posting omits pay, a parallel agent sweeps
  multiple sources (Glassdoor, Levels.fyi, AmbitionBox, Payscale) and reconciles
  them.
- **Relevance checking** — a keyword scorer *plus* an LLM cross-check vet every
  job against your résumé brief, so off-field roles (HR, content, sales) are
  dropped before they reach the spreadsheet.
- **Spreadsheet** — every search writes a `jobs.xlsx` with company, salary,
  qualifications, and link.
- **Two apply modes** — fully autonomous, or human-in-the-loop (you pick from the list).
- **Ask-once memory** — anything not on your résumé is asked once and remembered
  forever, then reused to auto-answer applications.
- **Broad search** — finds *all* matching jobs, not just LinkedIn Easy Apply;
  external-application jobs are included and handled at apply time
  (`--easy-only` to restrict).
- **Handles the hassle** — LinkedIn Easy Apply, external career sites, Google
  Forms, and account signup (reads verification codes from Gmail).
- **Resilient** — detects mid-run session expiry / security checks and resumes;
  everything is persisted so no progress is lost.

---

## 🔍 How it works

```
 résumé (pdf/docx) ──▶ extract ──▶ role analyzer ──▶ profile.json + candidate_brief.md
                                                          │
             onboarding (only what's missing) ───────────┤
                                                          ▼
                                    constraints + relevance gate   ◀── your hard rules
                                                          │
 LinkedIn (your session) ──▶ search ──▶ enrich (salary, web) ──▶ jobs.xlsx
                                                          │
                                                          ▼
                                      application engine (auto | you-pick)
                                          ├─ Easy Apply
                                          ├─ Google Form
                                          └─ external site (+ signup via Gmail codes)
```

---

## 📦 Installation

**Prerequisites:** Python 3.11+, and Google Chrome installed.

```bash
# 1. Clone
git clone https://github.com/akshath-raj/Job-Hunter.git
cd Job-Hunter

# 2. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate

# 3. Install the package
pip install -e .

# 4. Install the browser Playwright drives
playwright install chromium

# 5. Configure (see next section)
cp .env.example .env
```

Two commands are now on your PATH: **`job-hunter`** (the CLI) and
**`job-hunter-mcp`** (the MCP server).

---

## ⚙️ Configuration

Edit `.env`. Only a provider key is required, and only for standalone runs
(Claude Code needs none). `.env` is auto-loaded; real exported env vars win.

| Variable | Required? | Purpose |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | one provider | Use Anthropic as the standalone brain |
| `OPENAI_API_KEY` | one provider | Use OpenAI as the standalone brain |
| `JOBHUNTER_PROVIDER` | no | Force `anthropic` or `openai` (else: whichever key is set) |
| `JOBHUNTER_MODEL` | no | Override the default model |
| `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` | no | Auto-read signup verification codes ([App Password](https://myaccount.google.com/apppasswords), never your real password) |
| `JOBHUNTER_MIN_RELEVANCE` | no | Relevance cutoff `0-1` for dropping off-target jobs (default `0.22`) |
| `JOBHUNTER_HOME` | no | Where local state lives (default `~/.jobhunter`) |
| `JOBHUNTER_CDP_URL` | no | Attach to a running Chrome instead of launching one ([details](#-reusing-your-existing-chrome-login)) |

**Provider selection:** set either key. If you set both, Anthropic wins unless
`JOBHUNTER_PROVIDER=openai`. Defaults: `claude-sonnet-4-6` / `gpt-4o`.

```env
# Minimal .env for standalone use with OpenAI:
JOBHUNTER_PROVIDER=openai
OPENAI_API_KEY=sk-...
```

---

## 🚀 Usage

### Standalone CLI

A first run, step by step:

```bash
# 1. Log into LinkedIn once — opens a browser; the session persists.
job-hunter login

# 2. Understand your résumé. Extracts everything; asks only what's missing.
job-hunter onboard -r ~/resume.pdf -d "remote backend roles, no crypto"

# 3. (Optional) See how it understood you.
job-hunter brief

# 4. Search LinkedIn. Asks salary/location/preferences once (LLM-processed into
#    a search strategy), researches salaries in parallel, filters off-target
#    jobs, and writes ./jobs.xlsx.
job-hunter search --max 20

# 5. Review what it found and what it filtered out.
job-hunter jobs --status eligible
job-hunter jobs --status ineligible

# 6a. AUTONOMOUS: apply to your top matches with no prompts.
job-hunter apply --limit 5

# 6b. HUMAN-IN-THE-LOOP: list the jobs and choose which to apply to.
job-hunter apply --mode select
```

Or run the whole pipeline at once:

```bash
job-hunter run -r ~/resume.pdf -d "new-grad ML roles, US only" --limit 5
```

### As an MCP server (Claude Code)

No API key needed — **Claude Code is the brain**. Register the server once:

```bash
claude mcp add job-hunter -s user -- /absolute/path/to/Job-Hunter/.venv/bin/job-hunter-mcp
claude mcp list          # should show: job-hunter … ✔ Connected
```

Then just talk to Claude Code:

> *"Using job-hunter, onboard me from ~/resume.pdf — I'm a final-year student
> looking for remote ML internships. Search LinkedIn, show me the matches, and
> let me pick which to apply to."*

Claude chains the tools, asks you for anything missing, researches salaries with
web search, and handles unusual application forms by driving the browser
directly. To see everything it can do, just ask Claude *"what job-hunter tools do
you have?"*.

---

## 📖 Command reference

| Command | What it does |
| --- | --- |
| `login` | Sign into LinkedIn once (session persists). |
| `onboard -r <resume> [-d <notes>]` | Analyze résumé → profile + brief; ask only missing required fields. |
| `brief` | Print the detailed candidate brief the search agent uses. |
| `profile` | Show the full stored profile as JSON. |
| `search [--max N] [-q <query>]` | Search LinkedIn (all jobs by relevance), research salaries, filter, write `jobs.xlsx`. |
| `search --recent-days N` / `--headless` | Limit to recent postings / run without a visible browser. |
| `jobs [--status <s>]` | List stored jobs (`eligible`, `ineligible`, `applied`, …). |
| `enrich [--limit N]` | (Re)research company/salary/qualifications for stored jobs. |
| `export [--status <s>] [--path <p>]` | Write jobs to an Excel spreadsheet. |
| `apply [--limit N]` | **Autonomous** apply to top eligible matches. |
| `apply --mode select` | **Human-in-the-loop** — list jobs, you pick. |
| `apply --job <id>` / `--concurrency N` | Apply to one job / apply several in parallel. |
| `status` | Counts of jobs by status. |
| `reset [--keep-login] [--only <scope>]` | Delete stored data about you (asks to confirm). |

Run `job-hunter <command> --help` for all flags.

---

## 🔐 Reusing your existing Chrome login

By default the tool runs Chrome with an **isolated profile** so it never
conflicts with your everyday browser — you log into LinkedIn once. Chrome locks a
profile to one process, so it can't share your live default profile while your
normal Chrome is open.

To reuse your **already-logged-in** session, attach over CDP:

```bash
# 1. Quit Chrome, relaunch with a debugging port (macOS):
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --remote-debugging-port=9222
# 2. Point the tool at it:
export JOBHUNTER_CDP_URL=http://localhost:9222
```

It then drives your real profile and **won't close your browser** on exit.

---

## 🗂️ Your data & privacy

Everything is stored **locally** under `~/.jobhunter/` (override with
`JOBHUNTER_HOME`): your profile, résumé text, candidate brief, the jobs database,
the persistent browser session, and submission screenshots. The `jobs.xlsx`
spreadsheet is written to the folder you run from. Nothing is sent anywhere
except LinkedIn, your chosen LLM provider, and (if configured) Gmail.

Wipe it any time:

```bash
job-hunter reset                 # delete everything (asks to confirm)
job-hunter reset --keep-login    # keep only the LinkedIn session
job-hunter reset --only jobs     # clear one scope: profile|jobs|session|artifacts|spreadsheet
```

---

## ⚠️ Safety & limitations

- **Terms of Service.** LinkedIn's User Agreement prohibits automated scraping and
  applying. This tool drives *your own* logged-in Chrome at a human pace to reduce
  risk, but using it can still get your account restricted or banned.
- **Real submissions.** Autonomous mode submits real applications under your
  identity. Start with `--limit 1` and check the `~/.jobhunter/artifacts/`
  screenshots.
- **Best-effort on hard cases.** Easy Apply is the reliable path; external sites
  and Google Forms are best-effort and pause as `needs_input` rather than guess.
- **Go slow.** Aggressive automation triggers LinkedIn's security checks. Keep
  `--concurrency` low and `--max` modest.

---

## 🛠️ Development

```bash
pip install -e ".[dev]"
pytest              # fast, offline — no network, browser, or API keys
ruff check .
```

Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for the project
layout and guidelines.

---

## 📄 License

[MIT](LICENSE) © 2026 Akshath
