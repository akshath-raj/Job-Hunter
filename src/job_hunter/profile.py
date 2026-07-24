"""Load, save, and complete the user profile.

`missing_required_fields()` powers the interactive onboarding: whatever the
resume analysis couldn't fill, the CLI (or Claude Code via MCP) asks the user
before any search begins.
"""

from __future__ import annotations

import re

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


# ---- ask-once persistent memory (the `extra` store) -----------------------

# Common things not usually on a resume that applications ask for. Proactively
# collected during onboarding so we don't stall mid-application later.
COMMON_EXTRA = {
    "10th grade percentage or GPA": "What was your 10th grade / secondary school percentage?",
    "12th grade percentage or GPA": "What was your 12th grade / higher-secondary percentage?",
    "undergraduate cgpa or gpa": "What is your undergraduate CGPA / GPA?",
    "notice period": "What is your notice period / earliest start date?",
    "expected salary": "What is your expected salary (leave blank to skip)?",
}


_STOP = {
    "what", "is", "are", "was", "were", "your", "the", "please", "enter", "for",
    "you", "do", "did", "have", "has", "provide", "tell", "us", "me", "and", "of",
}


def normalize_question(question: str) -> str:
    return " ".join(question.lower().strip().split())


def _significant_tokens(question: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", normalize_question(question))
    return {w for w in words if w not in _STOP and len(w) > 2}


def _numeric_tokens(tokens: set[str]) -> set[str]:
    return {t for t in tokens if any(ch.isdigit() for ch in t)}


def remember(profile: Profile, question: str, answer: str) -> None:
    """Persist an answer to an arbitrary question so it's never asked again."""
    if answer not in (None, ""):
        profile.extra[normalize_question(question)] = answer


def recall(profile: Profile, question: str) -> str | None:
    """Find a stored answer, tolerant of phrasing but never conflating distinct
    numbered questions (e.g. 10th vs 12th grade)."""
    key = normalize_question(question)
    if key in profile.extra:
        return profile.extra[key]

    q_tokens = _significant_tokens(question)
    q_nums = _numeric_tokens(q_tokens)
    for stored_q, ans in profile.extra.items():
        s_tokens = _significant_tokens(stored_q)
        if not q_tokens or not s_tokens:
            if stored_q in key or key in stored_q:
                return ans
            continue
        # Differing numeric/ordinal tokens => different questions (10th vs 12th).
        if q_nums ^ _numeric_tokens(s_tokens):
            continue
        shared = q_tokens & s_tokens
        ratio = len(shared) / min(len(q_tokens), len(s_tokens))
        if len(shared) >= 2 and ratio >= 0.6:
            return ans
    return None


def missing_extra_fields(profile: Profile) -> dict[str, str]:
    """Common extra fields not yet answered (via fuzzy recall), as {key: question}."""
    return {k: q for k, q in COMMON_EXTRA.items() if recall(profile, k) is None}


def apply_extra(profile: Profile, answers: dict[str, str]) -> Profile:
    """Store a batch of {question: answer} into the persistent extra memory."""
    for question, answer in answers.items():
        remember(profile, question, answer)
    return profile


# ---- one-time job-search preferences (not derivable from a resume) --------

SEARCH_PREF_QUESTIONS = {
    "expected salary": "What's your expected salary / compensation? (e.g. '20 LPA', or blank)",
    "preferred locations": "Preferred job locations, comma-separated? (or blank for any)",
    "remote only": "Only remote roles? (y/N)",
}


def needs_search_preferences(profile: Profile) -> bool:
    return not profile.search_prefs_collected


def set_search_preferences(
    profile: Profile,
    expected_salary: str | None = None,
    locations: str | list[str] | None = None,
    remote_only: bool | None = None,
) -> Profile:
    """Apply search-time preferences and mark them collected (so we ask once)."""
    if expected_salary:
        remember(profile, "expected salary", expected_salary)
    if locations:
        locs = locations if isinstance(locations, list) else [
            x.strip() for x in locations.split(",") if x.strip()
        ]
        if locs:
            profile.constraints.locations = locs
    if remote_only is not None:
        profile.constraints.remote_only = remote_only
    profile.search_prefs_collected = True
    return profile
