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

ANALYSIS_SYSTEM = """You are a world-class technical recruiter and career analyst. \
You read a résumé the way a hiring manager in the candidate's own field would — \
you can tell a computer-vision researcher from an MLOps engineer from a \
full-stack developer, and you know the difference matters.

Your job: build a precise, evidence-based picture of what THIS candidate actually \
does — their domain, the concrete problems they've solved, the tools and \
techniques they use, the scale/impact of their work, and their real seniority — \
and turn it into a search strategy that finds the RIGHT jobs, not just any jobs.

Rules:
- Be specific, never generic. Ground every claim in the résumé. The search \
keywords must match the candidate's true specialization (e.g. "Machine Learning \
Engineer", "Computer Vision Intern", "Applied Scientist" — NOT a catch-all \
"Software Engineer"). Broad/wrong keywords are the #1 failure to avoid.
- The brief must be genuinely detailed and useful to a downstream search agent \
that has never seen the résumé — cite real projects, technologies, and results.
- Infer seniority conservatively. A current student or a future graduation date \
means the candidate is a student and is NOT eligible for senior/staff/lead roles \
— cap them (interns/new-grad/entry only).

Seniority must be one of: intern, entry, mid, senior, staff, lead, exec.
"""

ANALYSIS_INSTRUCTIONS = """Return a JSON object with exactly these keys:
{
  "target_roles": [string],        // 2-5 concrete job titles that FIT this candidate
  "search_keywords": [string],     // 3-6 exact LinkedIn search strings, specific to their
                                   // specialization (used verbatim as the job search queries)
  "domains": [string],             // fields/domains they work in, e.g. "Computer Vision", "NLP"
  "core_competencies": [string],   // 5-10 defining skills/areas (used for relevance scoring)
  "brief": string,                 // a DETAILED markdown brief (250-450 words) with sections:
                                   //   ## Who they are   (field, level, one-line positioning)
                                   //   ## Experience & projects  (SPECIFIC work, tech, results
                                   //      taken from the résumé — no filler)
                                   //   ## Core strengths
                                   //   ## Ideal roles & why they fit
                                   //   ## What to avoid  (off-target roles, deal-breakers)
                                   // This is the search agent's only context — make it excellent.
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
    # Generous token budget: the detailed brief + extracted details must not be
    # truncated (a cut-off brief produces broken JSON / half sentences).
    return llm.complete_json(
        ANALYSIS_SYSTEM, build_prompt(resume_text, description), max_tokens=4000
    )


def _coerce_seniority(value: str | None) -> Seniority | None:
    if not value:
        return None
    try:
        return Seniority(value.strip().lower())
    except ValueError:
        return None


_SEARCH_START = "<!-- search-preferences:start -->"
_SEARCH_END = "<!-- search-preferences:end -->"


def persist_brief(data: dict[str, Any]) -> str | None:
    """Write the résumé brief to candidate_brief.md, preserving any appended
    search-preferences section (we only replace the résumé part)."""
    from .. import config

    brief = (data.get("brief") or "").strip()
    if not brief:
        return None
    config.ensure_dirs()

    # Keep an existing search-preferences section if present.
    search_section = ""
    if config.BRIEF_PATH.exists():
        old = config.BRIEF_PATH.read_text()
        if _SEARCH_START in old:
            search_section = "\n\n" + _SEARCH_START + old.split(_SEARCH_START, 1)[1]
    config.BRIEF_PATH.write_text(brief + search_section)
    return str(config.BRIEF_PATH)


def update_brief_search_section(section_md: str) -> None:
    """Append/replace the search-preferences section WITHOUT touching the résumé
    brief above it — so the user's answers are added, never overwriting."""
    from .. import config

    config.ensure_dirs()
    base = config.BRIEF_PATH.read_text() if config.BRIEF_PATH.exists() else ""
    if _SEARCH_START in base:
        base = base.split(_SEARCH_START, 1)[0].rstrip()
    block = f"\n\n{_SEARCH_START}\n## Search preferences\n\n{section_md.strip()}\n{_SEARCH_END}\n"
    config.BRIEF_PATH.write_text(base.rstrip() + block)


def apply_analysis(profile: Profile, data: dict[str, Any]) -> Profile:
    """Merge an analysis dict into the profile without overwriting user-set fields."""
    profile.target_roles = data.get("target_roles") or profile.target_roles
    profile.search_keywords = data.get("search_keywords") or profile.search_keywords
    profile.domains = data.get("domains") or profile.domains
    profile.core_competencies = data.get("core_competencies") or profile.core_competencies
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

    persist_brief(data)
    return profile
