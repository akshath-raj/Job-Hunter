"""Role-analyzer agent: resume text -> structured understanding of the candidate.

The prompt is shared so both runtimes produce the same shape:
  * standalone  -> `analyze()` calls the Anthropic API.
  * via MCP     -> Claude Code calls `build_prompt()`, reasons itself, then hands
                   the JSON back through `apply_analysis()`.
"""

from __future__ import annotations

from typing import Any

from .. import llm
from ..models import Profile, Seniority

ANALYSIS_SYSTEM = """You are a career analyst. Given the text of a candidate's resume \
and optional free-text notes about what they want, produce a compact structured \
profile used to search and apply for jobs on their behalf.

Infer conservatively. If the resume shows a current student or an expected \
graduation date in the future, the candidate is a student and is NOT eligible for \
senior/staff/lead roles — cap them appropriately (interns/new-grad/entry only).

Seniority must be one of: intern, entry, mid, senior, staff, lead, exec.
"""

ANALYSIS_INSTRUCTIONS = """Return a JSON object with exactly these keys:
{
  "target_roles": [string],        // 2-5 concrete job titles to search, e.g. "Backend Engineer"
  "seniority": string,             // the candidate's OWN level (one of the allowed values)
  "max_seniority": string,         // highest level they should apply to (student => "entry")
  "skills": [string],              // top ~15 skills/technologies
  "years_experience": number,      // professional YoE (internships count as ~0.5x)
  "is_student": boolean,
  "graduation_date": string|null,  // ISO date if known
  "summary": string,               // 2-3 sentence candidate pitch, first person
  "inferred_full_name": string|null,
  "inferred_email": string|null,
  "inferred_phone": string|null,
  "inferred_location": string|null,
  "inferred_linkedin_url": string|null,
  "inferred_github_url": string|null,

  // Extract EVERY concrete fact an application might reuse and that IS present in
  // the resume. Use clear, self-describing keys. Omit anything not in the resume
  // (do NOT guess). Examples of keys to use when present:
  //   "10th grade percentage", "12th grade percentage", "undergraduate cgpa",
  //   "degree", "major", "university", "graduation year", "current employer",
  //   "current title", "certifications", "languages", "date of birth", "gender".
  "extracted_details": { string: string }
}"""


def build_prompt(resume_text: str, description: str | None) -> str:
    desc = f"\n\nCandidate's own notes about what they want:\n{description}" if description else ""
    return f"RESUME:\n{resume_text[:20000]}{desc}\n\n{ANALYSIS_INSTRUCTIONS}"


def analyze(resume_text: str, description: str | None = None) -> dict[str, Any]:
    """Standalone path: run the analysis through the LLM."""
    return llm.complete_json(ANALYSIS_SYSTEM, build_prompt(resume_text, description))


def _coerce_seniority(value: str | None) -> Seniority | None:
    if not value:
        return None
    try:
        return Seniority(value.strip().lower())
    except ValueError:
        return None


def apply_analysis(profile: Profile, data: dict[str, Any]) -> Profile:
    """Merge an analysis dict into the profile without overwriting user-set fields."""
    profile.target_roles = data.get("target_roles") or profile.target_roles
    profile.seniority = _coerce_seniority(data.get("seniority")) or profile.seniority
    profile.skills = data.get("skills") or profile.skills
    if data.get("years_experience") is not None:
        profile.years_experience = float(data["years_experience"])
    profile.summary = data.get("summary") or profile.summary

    c = profile.constraints
    if data.get("is_student"):
        c.is_student = True
    if data.get("graduation_date"):
        c.graduation_date = data["graduation_date"]
    max_sen = _coerce_seniority(data.get("max_seniority"))
    if max_sen and c.max_seniority is None:
        c.max_seniority = max_sen
    # Students never apply above entry level.
    entry_ok = {Seniority.intern, Seniority.entry}
    if c.is_student and (c.max_seniority is None or c.max_seniority not in entry_ok):
        c.max_seniority = Seniority.entry

    # Fill identity only where the user hasn't already provided a value.
    ident = profile.identity
    for field, key in [
        ("full_name", "inferred_full_name"),
        ("email", "inferred_email"),
        ("phone", "inferred_phone"),
        ("location", "inferred_location"),
        ("linkedin_url", "inferred_linkedin_url"),
        ("github_url", "inferred_github_url"),
    ]:
        if getattr(ident, field) in (None, "") and data.get(key):
            setattr(ident, field, data[key])

    # Everything else the resume contained (10th/12th marks, CGPA, degree, ...)
    # goes into the ask-once memory so applications reuse it and we never ask.
    from .. import profile as profile_mod

    details = data.get("extracted_details") or {}
    if isinstance(details, dict):
        for key, value in details.items():
            if isinstance(value, (str, int, float)) and str(value).strip():
                profile_mod.remember(profile, str(key), str(value))

    return profile
