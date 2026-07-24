"""Load, save, and complete the user profile.

`missing_required_fields()` powers the interactive onboarding: whatever the
resume analysis couldn't fill, the CLI (or Claude Code via MCP) asks the user
before any search begins.
"""

from __future__ import annotations

from . import config
from .models import Profile


def load() -> Profile:
    if config.PROFILE_PATH.exists():
        return Profile.model_validate_json(config.PROFILE_PATH.read_text())
    return Profile()


def save(profile: Profile) -> None:
    from .models import _now

    config.ensure_dirs()
    profile.updated_at = _now()
    config.PROFILE_PATH.write_text(profile.model_dump_json(indent=2))


def exists() -> bool:
    return config.PROFILE_PATH.exists()


# Fields we genuinely need before applying, with a human-friendly question each.
REQUIRED = {
    "identity.full_name": "What is your full name (as it should appear on applications)?",
    "identity.email": "What email should applications use?",
    "identity.phone": "What phone number should applications use?",
    "identity.location": "What is your current location (city, country)?",
}

RECOMMENDED = {
    "identity.linkedin_url": "What is your LinkedIn profile URL?",
    "constraints.work_authorization": "What is your work authorization / citizenship status?",
}


def _get(profile: Profile, dotted: str):
    obj = profile
    for part in dotted.split("."):
        obj = getattr(obj, part)
    return obj


def _set(profile: Profile, dotted: str, value) -> None:
    parts = dotted.split(".")
    obj = profile
    for part in parts[:-1]:
        obj = getattr(obj, part)
    setattr(obj, parts[-1], value)


def missing_required_fields(profile: Profile, include_recommended: bool = False) -> dict[str, str]:
    """Return {dotted_field: question} for everything still unset."""
    out: dict[str, str] = {}
    pool = dict(REQUIRED)
    if include_recommended:
        pool.update(RECOMMENDED)
    for field, question in pool.items():
        if _get(profile, field) in (None, ""):
            out[field] = question
    return out


def apply_answers(profile: Profile, answers: dict[str, str]) -> Profile:
    """Set fields from a {dotted_field: answer} map (from onboarding)."""
    for field, value in answers.items():
        if value not in (None, ""):
            _set(profile, field, value)
    return profile
