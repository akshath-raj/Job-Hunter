"""Verify an application URL is the company's official site or a trusted ATS —
a guardrail so we never fill credentials/PII into a look-alike / phishing page."""

from __future__ import annotations

import re
from urllib.parse import urlparse

# Legitimate applicant-tracking systems / form hosts companies genuinely use.
TRUSTED_ATS = (
    "greenhouse.io", "boards.greenhouse.io", "lever.co", "jobs.lever.co",
    "myworkdayjobs.com", "workday.com", "ashbyhq.com", "jobs.ashbyhq.com",
    "smartrecruiters.com", "icims.com", "taleo.net", "successfactors.com",
    "jobvite.com", "bamboohr.com", "workable.com", "recruitee.com",
    "teamtailor.com", "eightfold.ai", "phenom.com", "oraclecloud.com",
    "docs.google.com", "forms.gle", "linkedin.com", "naukri.com", "indeed.com",
    "wellfound.com", "instahyre.com", "hirist.com",
)

_STOP = {"inc", "llc", "ltd", "technologies", "technology", "labs", "the", "and",
         "pvt", "private", "limited", "corp", "corporation", "co", "solutions",
         "software", "systems", "global", "india"}


def _tokens(name: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", (name or "").lower())
            if len(t) > 2 and t not in _STOP]


def check(url: str, company: str) -> tuple[bool, str]:
    """Return (trusted, reason)."""
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False, "no host in URL"
    if any(host == d or host.endswith("." + d) for d in TRUSTED_ATS):
        return True, f"trusted application host ({host})"
    domain = host[4:] if host.startswith("www.") else host
    labels = set(domain.split("."))          # exact-label match, not substring
    for tok in _tokens(company):
        if tok in labels:
            return True, f"company name matches domain ({host})"
    return False, f"unverified domain '{host}' — not obviously {company}'s official site"
