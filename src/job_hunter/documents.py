"""Document guardrails — the safety layer for file uploads.

Hard rules:
  * The agent may ONLY upload files the user explicitly registered (the resume,
    plus anything added via `register`). Never a file the user didn't grant.
  * Sensitive documents (Aadhaar, passport, PAN, government/national ID, SSN,
    driver's license, voter ID) are NEVER assumed. If an application asks for
    one, the agent must stop and get the user's consent + a path first.
"""

from __future__ import annotations

import os

from .models import Profile

# term (lowercase) -> friendly name of a sensitive government / identity document.
SENSITIVE = {
    "aadhaar": "Aadhaar", "aadhar": "Aadhaar", "uidai": "Aadhaar",
    "pan card": "PAN card", "pan number": "PAN", "passport": "passport",
    "government id": "government ID", "govt id": "government ID",
    "national id": "national ID", "national identity": "national ID",
    "ssn": "SSN", "social security": "SSN",
    "driver": "driver's license", "driving licen": "driver's license",
    "voter": "voter ID",
}


def register(profile: Profile, name: str, path: str) -> bool:
    """Allowlist a document. Returns False if the file doesn't exist."""
    p = os.path.abspath(os.path.expanduser(path.strip()))
    if not os.path.isfile(p):
        return False
    profile.documents[name.strip().lower()] = p
    return True


def allowed_paths(profile: Profile) -> set[str]:
    """The set of real paths the agent is permitted to upload."""
    paths = set(profile.documents.values())
    if profile.identity.resume_path:
        paths.add(os.path.expanduser(profile.identity.resume_path))
    out = set()
    for p in paths:
        try:
            out.add(os.path.realpath(p))
        except Exception:  # noqa: BLE001
            pass
    return out


def is_allowed(profile: Profile, path: str) -> bool:
    """True only if `path` is one of the user's registered documents."""
    try:
        return os.path.realpath(os.path.expanduser(path)) in allowed_paths(profile)
    except Exception:  # noqa: BLE001
        return False


def sensitive_kind(label: str) -> str | None:
    """If a form field is asking for a sensitive gov/identity doc, name it."""
    low = (label or "").lower()
    for term, kind in SENSITIVE.items():
        if term in low:
            return kind
    return None


def resolve_upload(profile: Profile, field_label: str) -> tuple[str | None, str | None]:
    """Decide what to upload for a file field.

    Returns (path, needs_consent_prompt). Exactly one is non-None:
      * path            -> an allowlisted file to upload, or
      * consent prompt  -> stop and ask the user (sensitive doc, or nothing fits).
    """
    kind = sensitive_kind(field_label)
    if kind:
        # Only use a sensitive doc the user explicitly registered under that name.
        for name, path in profile.documents.items():
            if kind.lower().split()[0] in name and is_allowed(profile, path):
                return path, None
        return None, (
            f"This application asks for your {kind}. I will NOT upload any ID "
            f"without your say-so. If you're OK using it, register it: "
            f"`job-hunter document add {kind.split()[0].lower()} <path>` and re-run."
        )
    # Non-sensitive file field (resume/CV/cover letter): default to the resume.
    label = (field_label or "").lower()
    if any(w in label for w in ("resume", "cv", "curriculum")):
        if profile.identity.resume_path and is_allowed(profile, profile.identity.resume_path):
            return os.path.expanduser(profile.identity.resume_path), None
    # A cover letter or other named doc, if registered.
    for name, path in profile.documents.items():
        if name in label and is_allowed(profile, path):
            return path, None
    # Fall back to the resume for a generic "upload document" field.
    if profile.identity.resume_path and is_allowed(profile, profile.identity.resume_path):
        return os.path.expanduser(profile.identity.resume_path), None
    return None, f"The form wants a file for '{field_label}' but nothing suitable is registered."
