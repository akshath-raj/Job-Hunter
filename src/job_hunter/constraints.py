"""Eligibility engine: decide whether a job satisfies ALL of the user's hard rules.

This is the safety gate the user cares about most: "if he is a college student he
cannot apply for senior roles." Every job passes through `check()` before the
application engine is ever allowed to touch it. A single failed rule = skip.
"""

from __future__ import annotations

import re

from .models import SENIORITY_ORDER, Constraints, Job, Profile, Seniority

# Words in a posting that imply a seniority level, most-senior first so the
# first match wins (a "Senior Staff" title resolves to staff, not senior).
_SENIORITY_SIGNALS: list[tuple[Seniority, list[str]]] = [
    (Seniority.exec, ["vp ", "vice president", "chief", "cto", "ceo", "head of"]),
    (Seniority.lead, ["director", "manager", "team lead", "tech lead", "engineering lead"]),
    (Seniority.staff, ["staff", "principal", "distinguished", "architect"]),
    (Seniority.senior, ["senior", "sr.", "sr ", "iii", "lead engineer"]),
    (Seniority.mid, ["mid", "ii ", " ii", "intermediate"]),
    (Seniority.entry,
     ["junior", "jr.", "jr ", "entry", "new grad", "graduate", "associate", " i "]),
    (Seniority.intern, ["intern", "internship", "co-op", "trainee", "apprentice"]),
]


def infer_seniority(job: Job) -> Seniority | None:
    hay = f" {job.title.lower()} {(job.seniority_text or '').lower()} "
    for level, signals in _SENIORITY_SIGNALS:
        if any(s in hay for s in signals):
            return level
    return None


def _rank(s: Seniority) -> int:
    return SENIORITY_ORDER.index(s)


def check(job: Job, profile: Profile) -> tuple[bool, str | None]:
    """Return (eligible, reason_if_not)."""
    c: Constraints = profile.constraints
    title = job.title.lower()
    desc = (job.description or "").lower()
    blob = f"{title}\n{desc}\n{job.company.lower()}"

    # 1. Excluded companies.
    for company in c.exclude_companies:
        if company.lower() in job.company.lower():
            return False, f"excluded company ({company})"

    # 2. Excluded keywords (e.g. "requires PhD", "security clearance").
    for kw in c.exclude_keywords:
        if kw.lower() in blob:
            return False, f"contains excluded keyword ({kw})"

    # 3. Seniority ceiling — the core student-safety rule.
    ceiling = c.max_seniority
    if c.is_student and ceiling is None:
        ceiling = Seniority.entry
    job_level = infer_seniority(job)
    if ceiling is not None and job_level is not None:
        if _rank(job_level) > _rank(ceiling):
            return False, f"seniority '{job_level.value}' above ceiling '{ceiling.value}'"
    if c.allowed_seniorities and job_level is not None and job_level not in c.allowed_seniorities:
        return False, f"seniority '{job_level.value}' not in allowed set"

    # 4. Common hard experience gates in the description ("8+ years").
    if profile.years_experience is not None:
        for m in re.finditer(r"(\d{1,2})\+?\s*(?:years|yrs)", desc):
            required = int(m.group(1))
            if required - profile.years_experience > 2:  # allow a 2yr stretch
                return False, f"requires ~{required}y experience (have {profile.years_experience})"
            break

    # 5. Remote / location.
    wp = (job.workplace_type or "").lower()
    if c.remote_only and "remote" not in wp and "remote" not in (job.location or "").lower():
        return False, "not remote"
    if c.locations:
        loc_hay = f"{(job.location or '').lower()} {wp}"
        allows_remote = any(_expand_location(loc) & {"remote"} for loc in c.locations)
        ok = "remote" in loc_hay and allows_remote
        if not ok:
            for loc in c.locations:
                if any(alias in loc_hay for alias in _expand_location(loc)):
                    ok = True
                    break
        if not ok:
            return False, f"location '{job.location}' not in {c.locations}"

    return True, None


# City/region aliases so "Bangalore" matches "Bengaluru, Karnataka, India", etc.
_LOCATION_ALIASES: dict[str, set[str]] = {
    "bangalore": {"bangalore", "bengaluru"},
    "bengaluru": {"bangalore", "bengaluru"},
    "mumbai": {"mumbai", "bombay"},
    "bombay": {"mumbai", "bombay"},
    "delhi": {"delhi", "new delhi", "ncr", "gurgaon", "gurugram", "noida"},
    "gurgaon": {"gurgaon", "gurugram"},
    "gurugram": {"gurgaon", "gurugram"},
    "hyderabad": {"hyderabad", "secunderabad"},
    "pune": {"pune", "poona"},
    "kolkata": {"kolkata", "calcutta"},
    "chennai": {"chennai", "madras"},
    "bengaluru/bangalore": {"bangalore", "bengaluru"},
    "remote": {"remote", "anywhere", "work from home", "wfh"},
    "anywhere": {"remote", "anywhere"},
}


def _expand_location(loc: str) -> set[str]:
    key = loc.strip().lower()
    return _LOCATION_ALIASES.get(key, {key})


def annotate(job: Job, profile: Profile) -> Job:
    """Set job.status to eligible/ineligible with a reason. Mutates and returns."""
    from .models import JobStatus

    ok, reason = check(job, profile)
    job.status = JobStatus.eligible if ok else JobStatus.ineligible
    job.ineligible_reason = reason
    return job
