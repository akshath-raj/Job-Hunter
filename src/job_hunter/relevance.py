"""Score how well a scraped job matches the candidate — and drop the off-target
ones. LinkedIn keyword search casts a wide net; this is the filter that keeps
"Machine Learning Engineer" and rejects the random "Sales Engineer" that merely
shared a word.

Scoring is deterministic (no API cost): overlap of the profile's defining terms
(target roles, domains, competencies, skills) with the job title (weighted) and
description, plus an "anchor" rule — if no role/domain word appears in the title,
the job is almost certainly off-target and is heavily penalized.
"""

from __future__ import annotations

import re

from . import config
from .models import Job, JobStatus, Profile

_STOP = {
    "the", "and", "for", "with", "you", "our", "are", "will", "your", "job",
    "role", "work", "team", "engineer", "developer", "intern", "internship",
}


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9+#.]+", text.lower()))


def _significant(terms: list[str]) -> set[str]:
    words: set[str] = set()
    for term in terms:
        for w in re.findall(r"[a-z0-9+#.]+", (term or "").lower()):
            if len(w) > 2 and w not in _STOP:
                words.add(w)
    return words


def score(job: Job, profile: Profile) -> float:
    """Relevance in [0, 1]. 1.0 when we have nothing to compare against."""
    terms = profile.relevance_terms()
    profile_words = _significant(terms)
    if not profile_words:
        return 1.0

    title_tokens = _tokens(job.title)
    desc_tokens = _tokens(job.description or "")

    title_hits = len(profile_words & title_tokens)
    desc_hits = len(profile_words & desc_tokens)

    # Anchor: a role/domain identity word must appear in the title.
    anchors = _significant([*profile.target_roles, *profile.domains, *profile.search_keywords])
    has_anchor = bool(anchors & title_tokens) if anchors else True

    raw = title_hits * 3 + min(desc_hits, 8)
    s = min(1.0, raw / 8.0)
    if not has_anchor:
        s *= 0.35
    return round(s, 3)


def annotate(job: Job, profile: Profile) -> Job:
    """Set job.match_score and demote already-eligible jobs that are off-target."""
    s = score(job, profile)
    job.match_score = s
    if job.status == JobStatus.eligible and s < config.min_relevance():
        job.status = JobStatus.ineligible
        job.ineligible_reason = f"low relevance to your profile (score {s})"
    return job
