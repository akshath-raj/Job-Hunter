"""Final eligibility agent — runs after enrichment and classifies each job RAG:

  🔴 red    — we strictly CAN'T apply: qualifications not met, location, seniority,
              a deal-breaker, or the role is off-field.
  🟡 yellow — we CAN apply, but it misses a soft preference: salary below
              expectation, work-culture / description conditions, very competitive.
  🟢 green  — eligible and matches everything.

Hard/location/seniority checks are deterministic (constraints helpers). The
qualification match and soft description/culture fit use one LLM call per job —
these run concurrently, so it's effectively many small judgement agents.
`flags` combines every reason (hard first) into the single spreadsheet column.
"""

from __future__ import annotations

from . import config, constraints, fit
from .models import Job, Profile


def _llm_verdict(job: Job, profile: Profile) -> dict | None:
    """Ask the LLM whether the candidate is qualified + any soft concerns."""
    if not config.has_llm():
        return None
    from . import llm

    c = profile.constraints
    system = (
        "You decide whether a candidate can and should apply to a job.\n"
        "1) QUALIFIED: do they meet the job's HARD requirements (mandatory degree, "
        "core skills, minimum experience, field)? Be lenient — interns/new grads "
        "qualify for entry/intern roles; do NOT require 'nice to have' or "
        "'preferred' items. Mark unqualified ONLY for clear, hard gaps (e.g. a "
        "PhD or 8+ years is required and they lack it, or it's a different "
        "profession).\n"
        "2) SOFT_CONCERNS: list only ways the job FAILS a preference the candidate "
        "explicitly stated in their description/preferences (e.g. an industry or "
        "company type they want to avoid, a work style/culture they asked for). "
        "Do NOT mention salary at all — salary is handled separately. Do NOT invent "
        "concerns; if the job matches their preferences, return []. A job paying "
        "MORE than expected is GOOD, never a concern. Each concern must be a real, "
        "specific mismatch phrased from the candidate's side.\n"
        "Respond with ONLY JSON."
    )
    prompt = (
        f"CANDIDATE:\n{(profile.summary or '')}\n"
        f"Skills: {', '.join(profile.skills[:20])}\n"
        f"Level: {profile.seniority.value if profile.seniority else 'n/a'}\n"
        f"Preferences / description: {profile.description or profile.search_context or 'none'}\n"
        f"Deal-breakers: {', '.join(c.exclude_keywords) or 'none'}\n\n"
        f"JOB: {job.title} at {job.company} ({job.location or 'n/a'})\n"
        f"Work culture: {job.work_culture or 'unknown'}\n"
        f"Qualifications/JD:\n{job.qualifications or ''} {(job.description or '')[:1500]}\n\n"
        "Judge qualification (hard) and soft preference conflicts (NOT salary).\n"
        'Return {"qualified": bool, "qualification_gap": string, "soft_concerns": [string]}'
    )
    try:
        return llm.complete_json(system, prompt, max_tokens=400)
    except Exception:  # noqa: BLE001 — judgement is best-effort
        return None


def classify(job: Job, profile: Profile, use_llm: bool = True) -> tuple[str, list[str]]:
    """Return (rag, flags). rag in {"green","yellow","red"}."""
    c = profile.constraints
    hard: list[str] = []
    soft: list[str] = []

    # --- deterministic HARD blocks -------------------------------------
    for company in c.exclude_companies:
        if company.lower() in job.company.lower():
            hard.append(f"excluded company ({company})")
    blob = f"{job.title} {job.description or ''} {job.about or ''}".lower()
    for kw in c.exclude_keywords:
        if kw.lower() in blob:
            hard.append(f"deal-breaker '{kw}'")
    ok, reason = constraints.seniority_ok(job, profile)
    if not ok:
        hard.append(reason)
    ok, reason = constraints.workstyle_ok(job, profile)
    if not ok:
        hard.append(reason)
    ok, reason = constraints.location_ok(job, profile)
    if not ok:
        hard.append(reason)
    # Off-field (relevance scorer already ran).
    if job.match_score is not None and job.match_score < config.min_relevance():
        hard.append("role appears outside your field")

    # --- deterministic SOFT concerns -----------------------------------
    from . import profile as profile_mod

    expected = profile_mod.recall(profile, "expected salary")
    if expected and job.salary:
        sflag = fit._salary_flag(job.salary, expected)
        if sflag:
            soft.append(sflag)
    n = fit._applicant_count(job.num_applicants)
    if n is not None and n >= 150:
        soft.append(f"highly competitive ({job.num_applicants})")

    # --- LLM judgement: qualifications (hard) + soft fit ---------------
    if use_llm:
        v = _llm_verdict(job, profile)
        if v:
            if v.get("qualified") is False:
                gap = (v.get("qualification_gap") or "requirements not met").strip()
                hard.append(f"qualifications: {gap}")
            for sc in (v.get("soft_concerns") or []):
                text = str(sc).strip()
                # Salary is handled deterministically; drop any LLM salary talk
                # (it tends to get the direction wrong).
                if text and "salary" not in text.lower() and "pay" not in text.lower():
                    soft.append(text)

    # De-dupe while preserving order.
    def _dedupe(items: list[str]) -> list[str]:
        seen, out = set(), []
        for it in items:
            key = it.lower()
            if key not in seen:
                seen.add(key)
                out.append(it)
        return out

    hard, soft = _dedupe(hard), _dedupe(soft)
    # RED = can't apply: show ONLY the hard blockers (soft concerns are moot).
    if hard:
        return "red", hard
    return ("yellow", soft) if soft else ("green", [])
