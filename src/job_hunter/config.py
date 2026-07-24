"""Central config: where local state lives and how to reach the LLM.

All user state (profile, resume text, job database, and the persistent browser
session) lives under a single home directory so the tool is self-contained and
easy to reset. Default: ~/.jobhunter (override with JOBHUNTER_HOME).
"""

from __future__ import annotations

import os
from pathlib import Path


def _home() -> Path:
    override = os.environ.get("JOBHUNTER_HOME")
    if override:
        return Path(override).expanduser()
    # Prefer a visible ~/.jobhunter; fall back to platform data dir.
    return Path.home() / ".jobhunter"


HOME: Path = _home()
PROFILE_PATH: Path = HOME / "profile.json"
RESUME_TEXT_PATH: Path = HOME / "resume.txt"
DB_PATH: Path = HOME / "jobs.db"
BROWSER_PROFILE_DIR: Path = HOME / "chrome-profile"
LOG_DIR: Path = HOME / "logs"
ARTIFACTS_DIR: Path = HOME / "artifacts"  # screenshots of submitted apps


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
