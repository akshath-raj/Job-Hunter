"""Central config: where local state lives and how to reach the LLM.

All user state (profile, resume text, job database, and the persistent browser
session) lives under a single home directory so the tool is self-contained and
easy to reset. Default: ~/.jobhunter (override with JOBHUNTER_HOME).
"""

from __future__ import annotations

import os
from pathlib import Path

# Load a .env file so keys/provider set there "just work" without exporting.
# Real exported env vars still win (override=False). We look in the current
# working dir (and parents), then fall back to ~/.jobhunter/.env.
try:
    from dotenv import load_dotenv

    load_dotenv()  # cwd and parents
    _home_env = Path.home() / ".jobhunter" / ".env"
    if _home_env.exists():
        load_dotenv(_home_env, override=False)
except Exception:  # noqa: BLE001 — dotenv optional; never block on it
    pass


def _home() -> Path:
    override = os.environ.get("JOBHUNTER_HOME")
    if override:
        return Path(override).expanduser()
    # Prefer a visible ~/.jobhunter; fall back to platform data dir.
    return Path.home() / ".jobhunter"


HOME: Path = _home()
PROFILE_PATH: Path = HOME / "profile.json"
RESUME_TEXT_PATH: Path = HOME / "resume.txt"
BRIEF_PATH: Path = HOME / "candidate_brief.md"   # detailed profile for the search agent
DB_PATH: Path = HOME / "jobs.db"
BROWSER_PROFILE_DIR: Path = HOME / "chrome-profile"
LOG_DIR: Path = HOME / "logs"
ARTIFACTS_DIR: Path = HOME / "artifacts"  # screenshots of submitted apps


def min_relevance() -> float:
    """Jobs scoring below this (0-1) against the profile are dropped as off-target."""
    try:
        return float(os.environ.get("JOBHUNTER_MIN_RELEVANCE", "0.22"))
    except ValueError:
        return 0.22


def ensure_dirs() -> None:
    for p in (HOME, BROWSER_PROFILE_DIR, LOG_DIR, ARTIFACTS_DIR):
        p.mkdir(parents=True, exist_ok=True)


# --- LLM provider selection ------------------------------------------------
# The "brain" (resume analysis + answering application questions) can run on
# either Anthropic or OpenAI. Selection order:
#   1. explicit JOBHUNTER_PROVIDER ("anthropic" | "openai")
#   2. whichever API key is present (Anthropic wins ties)
#   3. default to anthropic
DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-4o",
}


def anthropic_key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY")


def openai_key() -> str | None:
    return os.environ.get("OPENAI_API_KEY")


def llm_provider() -> str:
    explicit = os.environ.get("JOBHUNTER_PROVIDER")
    if explicit:
        return explicit.strip().lower()
    if anthropic_key():
        return "anthropic"
    if openai_key():
        return "openai"
    return "anthropic"


def model_for(provider: str) -> str:
    override = os.environ.get("JOBHUNTER_MODEL")
    if override:
        return override
    return DEFAULT_MODELS.get(provider, DEFAULT_MODELS["anthropic"])


def has_llm() -> bool:
    """True if any provider is usable — i.e. standalone reasoning is possible."""
    return bool(anthropic_key() or openai_key())


# --- browser session options ----------------------------------------------
# By default we launch a real Chrome with an isolated profile under
# JOBHUNTER_HOME (log in once, no conflict with your everyday Chrome).
# Two opt-in overrides let you reuse your *existing* login instead:
#
#   JOBHUNTER_CDP_URL              attach to an already-running Chrome started
#                                  with --remote-debugging-port (uses that live
#                                  browser + real profile; no lock conflict).
#   JOBHUNTER_CHROME_USER_DATA_DIR launch against your real Chrome data dir
#   JOBHUNTER_CHROME_PROFILE       + a named profile (e.g. "Default").
#                                  Requires Chrome to be fully closed first.
def cdp_url() -> str | None:
    return os.environ.get("JOBHUNTER_CDP_URL") or None


def chrome_user_data_dir() -> str:
    return os.environ.get("JOBHUNTER_CHROME_USER_DATA_DIR") or str(BROWSER_PROFILE_DIR)


def chrome_profile_directory() -> str | None:
    return os.environ.get("JOBHUNTER_CHROME_PROFILE") or None
