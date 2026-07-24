"""Pydantic models — the shared vocabulary across agents, store, CLI, and MCP."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(UTC).isoformat()


class Seniority(StrEnum):
    """Coarse seniority ladder used for eligibility filtering."""

    intern = "intern"
    entry = "entry"          # new grad / junior
    mid = "mid"
    senior = "senior"
    staff = "staff"          # staff/principal
    lead = "lead"            # manager/director/lead
    exec = "exec"


SENIORITY_ORDER = [
    Seniority.intern,
    Seniority.entry,
    Seniority.mid,
    Seniority.senior,
    Seniority.staff,
    Seniority.lead,
    Seniority.exec,
]


class Constraints(BaseModel):
    """Hard rules an application MUST satisfy. Violations = skip, always."""

    is_student: bool = False
    graduation_date: str | None = None  # ISO date, if student
    max_seniority: Seniority | None = None      # e.g. student -> entry
    allowed_seniorities: list[Seniority] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)   # e.g. ["Remote", "Bangalore"]
    remote_only: bool = False                             # legacy; prefer workplace_types
    # Acceptable work styles: subset of {"onsite","hybrid","remote"}. Empty = any.
    workplace_types: list[str] = Field(default_factory=list)
    require_sponsorship: bool = False   # user needs visa sponsorship
    work_authorization: str | None = None   # e.g. "US Citizen", "F-1 OPT", "Indian citizen"
    exclude_companies: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)  # e.g. ["clearance", "PhD required"]
    min_salary: int | None = None


class UserIdentity(BaseModel):
    """Personal details used to fill applications. Collected once at onboarding."""

    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    portfolio_url: str | None = None
    resume_path: str | None = None


class Profile(BaseModel):
    """The complete user picture: who they are + what they want + hard rules."""

    identity: UserIdentity = Field(default_factory=UserIdentity)
    constraints: Constraints = Field(default_factory=Constraints)

    # Derived from resume by the role-analyzer agent:
    target_roles: list[str] = Field(default_factory=list)   # e.g. ["Backend Engineer"]
    search_keywords: list[str] = Field(default_factory=list)  # exact LinkedIn queries to run
    domains: list[str] = Field(default_factory=list)          # e.g. ["Computer Vision", "NLP"]
    core_competencies: list[str] = Field(default_factory=list)  # for relevance scoring
    seniority: Seniority | None = None
    skills: list[str] = Field(default_factory=list)
    years_experience: float | None = None
    summary: str | None = None       # one-paragraph pitch, reused in cover letters

    # Free-text extra wishes from the user (the `description` param):
    description: str | None = None

    # Whether we've collected job-search preferences that aren't on a resume
    # (salary expectation, locations, remote). Asked once, at first search.
    search_prefs_collected: bool = False

    # LLM-processed search strategy: a tight brief the search agent reads for
    # context (what to prioritize, company preferences, deal-breakers).
    search_context: str | None = None

    # Ask-once persistent memory. Anything not on the resume that an application
    # asked for (10th/12th marks, CGPA, notice period, ...) is remembered here,
    # keyed by a normalized question, and reused across every future session.
    extra: dict[str, str] = Field(default_factory=dict)

    updated_at: str = Field(default_factory=_now)

    def search_queries(self) -> list[str]:
        """Precise LinkedIn search strings — keywords first, then target roles."""
        return self.search_keywords or self.target_roles or (
            ["software engineer"] if not self.summary else []
        )

    def relevance_terms(self) -> list[str]:
        """Terms that define what's on-target for this candidate (for scoring)."""
        return [
            *self.target_roles, *self.search_keywords, *self.domains,
            *self.core_competencies, *self.skills,
        ]


class JobStatus(StrEnum):
    discovered = "discovered"
    eligible = "eligible"
    ineligible = "ineligible"
    applying = "applying"
    applied = "applied"
    needs_input = "needs_input"     # paused, waiting on the user
    failed = "failed"
    skipped = "skipped"


class Job(BaseModel):
    id: str                     # stable hash of (source, external_id)
    source: str = "linkedin"
    external_id: str            # LinkedIn job id
    url: str
    title: str
    company: str
    location: str | None = None
    workplace_type: str | None = None   # Remote / Hybrid / On-site
    seniority_text: str | None = None   # raw text from posting
    description: str | None = None
    easy_apply: bool = False
    posted_at: str | None = None

    status: JobStatus = JobStatus.discovered
    ineligible_reason: str | None = None
    match_score: float | None = None    # 0-1, how well it fits the profile
    discovered_at: str = Field(default_factory=_now)

    # Scraped from the posting:
    posted_ago: str | None = None       # e.g. "2 weeks ago"
    num_applicants: str | None = None   # e.g. "47 applicants" / "Over 200 applicants"

    # Enrichment (filled by a research agent / web search for the Excel export):
    about: str | None = None            # what the company does
    salary: str | None = None           # pay range WITH currency/country
    qualifications: str | None = None   # key required quals, summarized
    work_culture: str | None = None     # summarized from employee reviews
    pros: str | None = None             # positives from reviews (Glassdoor/AmbitionBox…)
    cons: str | None = None             # negatives from reviews
    enrichment_source: str | None = None  # where the research came from
    enriched: bool = False


class Application(BaseModel):
    job_id: str
    status: JobStatus
    submitted_at: str | None = None
    method: str | None = None           # easy_apply / external_form / google_form
    answers: dict[str, str] = Field(default_factory=dict)   # question -> answer log
    screenshot_path: str | None = None
    notes: str | None = None
    needs_input_prompt: str | None = None   # what we need from the user, if paused
    error: str | None = None
    updated_at: str = Field(default_factory=_now)
