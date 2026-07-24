"""Cross-check an enriched job against the user's stated requirements and flag
mismatches — the warnings that show up in the spreadsheet's Flags column.

Deterministic and cheap: salary vs expectation (best-effort, same-unit only),
deal-breaker keywords, and how competitive the posting is. The LLM enrichment
adds any further nuance via a `concerns` note, which the caller merges in.
"""

from __future__ import annotations

import re

from .models import Job, Profile


def _lpa_values(text: str) -> list[float]:
    return [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\s*(?:lpa|lakh)", text.lower())]


def _usd_k_values(text: str) -> list[float]:
    # "$120k", "120k", "$120,000" -> thousands
    vals: list[float] = []
    for m in re.finditer(r"\$?\s*([\d,]+(?:\.\d+)?)\s*k", text.lower()):
        vals.append(float(m.group(1).replace(",", "")))
    for m in re.finditer(r"\$\s*([\d,]{4,})", text):
        vals.append(float(m.group(1).replace(",", "")) / 1000)
    return vals


def _applicant_count(text: str | None) -> int | None:
    if not text:
        return None
    m = re.search(r"(\d[\d,]*)", text)
    return int(m.group(1).replace(",", "")) if m else None


def _salary_flag(job_salary: str, expected: str) -> str | None:
    """Flag when the job's top of range is below the user's expectation.

    Only compares when both look like the same unit (LPA or $k) to avoid
    nonsensical cross-currency comparisons.
    """
    for extract in (_lpa_values, _usd_k_values):
        exp_vals = extract(expected)
        job_vals = extract(job_salary)
        if exp_vals and job_vals:
            if max(job_vals) < min(exp_vals):
                return f"salary {job_salary} below your expectation ({expected})"
            return None
    return None


def check(job: Job, profile: Profile) -> list[str]:
    """Return a list of human-readable requirement mismatches for this job."""
    flags: list[str] = []
    c = profile.constraints

    # Salary vs expectation.
    from . import profile as profile_mod

    expected = profile_mod.recall(profile, "expected salary")
    if expected and job.salary:
        sf = _salary_flag(job.salary, expected)
        if sf:
            flags.append(sf)

    # Deal-breaker keywords surfacing in the posting/company info.
    blob = f"{(job.description or '')} {(job.about or '')} {(job.title or '')}".lower()
    for kw in c.exclude_keywords:
        if kw.lower() in blob:
            flags.append(f"contains deal-breaker '{kw}'")

    # Competitiveness.
    n = _applicant_count(job.num_applicants)
    if n is not None and n >= 100:
        flags.append(f"highly competitive ({job.num_applicants})")

    # Work style, if we know it and it isn't preferred (usually pre-filtered).
    wtypes = [w.lower() for w in c.workplace_types]
    wp = (job.workplace_type or "").lower()
    if wtypes and wp and not any(w in wp for w in wtypes):
        flags.append(f"work style '{job.workplace_type}' not preferred")

    return flags


def merge(flags: list[str], llm_concerns: str | None) -> str | None:
    """Combine deterministic flags with the LLM's concern note into one cell."""
    parts = list(flags)
    concern = (llm_concerns or "").strip()
    if concern and concern.lower() not in {"none", "n/a"}:
        parts.append(concern)
    return "; ".join(parts) if parts else None
