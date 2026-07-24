"""Answer application questions on the user's behalf.

Two tiers:
  1. Deterministic — name/email/phone/location/experience/authorization come
     straight from the profile. Fast, free, and never wrong.
  2. LLM fallback — anything else (free-text "why do you want this role?", odd
     dropdowns) is answered by Claude using the profile + job as context. If no
     LLM is available, unknown *required* questions cause a pause-for-input.

`external_answer` lets a host (Claude Code over MCP) inject an answer for a
specific question so the flow can resume without an API key.
"""

from __future__ import annotations

from ..models import Job, Profile


class ProfileAnswerer:
    def __init__(self, profile: Profile, job: Job | None = None, use_llm: bool = True):
        self.profile = profile
        self.job = job
        self.use_llm = use_llm
        self.overrides: dict[str, str] = {}  # question(lower) -> answer, injected by host

    def inject(self, question: str, answer: str) -> None:
        self.overrides[question.strip().lower()] = answer

    async def __call__(self, question: str, options: list[str] | None) -> str:
        q = question.lower().strip()

        if q in self.overrides:
            return self.overrides[q]

        det = self._deterministic(q, options)
        if det is not None:
            return det

        if self.use_llm:
            try:
                return self._llm_answer(question, options)
            except Exception:  # noqa: BLE001 — no key / network; fall through to pause
                return ""
        return ""

    def _deterministic(self, q: str, options: list[str] | None) -> str | None:
        ident = self.profile.identity
        c = self.profile.constraints

        def pick_yes_no(value: bool) -> str | None:
            if not options:
                return "Yes" if value else "No"
            want = "yes" if value else "no"
            for o in options:
                if want in o.lower():
                    return o
            return None

        if "email" in q and ident.email:
            return ident.email
        if ("phone" in q or "mobile" in q) and ident.phone:
            return ident.phone
        if ("first name" in q) and ident.full_name:
            return ident.full_name.split()[0]
        if ("last name" in q or "surname" in q) and ident.full_name:
            return ident.full_name.split()[-1]
        if ("full name" in q or q == "name") and ident.full_name:
            return ident.full_name
        if ("city" in q or "location" in q or "address" in q) and ident.location:
            return ident.location
        if "linkedin" in q and ident.linkedin_url:
            return ident.linkedin_url
        if ("github" in q or "portfolio" in q or "website" in q):
            return ident.github_url or ident.portfolio_url or ""

        # Years of experience — including "years of experience with X".
        if ("years" in q or "experience" in q) and self.profile.years_experience is not None:
            return str(int(round(self.profile.years_experience)))

        # Work authorization / sponsorship.
        if "sponsor" in q or "visa" in q:
            return pick_yes_no(c.require_sponsorship) or ""
        if "authori" in q or "legally" in q or "eligible to work" in q:
            # A Yes/No control needs Yes/No; a free-text box wants the detail.
            if options:
                return pick_yes_no(not c.require_sponsorship) or ""
            return c.work_authorization or ("No" if c.require_sponsorship else "Yes")

        # Common gating yes/no.
        if "are you willing to relocate" in q:
            return pick_yes_no(bool(self.profile.constraints.locations)) or "Yes"
        if any(k in q for k in ("18 years", "over 18", "at least 18")):
            return pick_yes_no(True) or ""

        # Numeric salary expectation.
        if "salary" in q and c.min_salary:
            return str(c.min_salary)

        return None

    def _llm_answer(self, question: str, options: list[str] | None) -> str:
        from .. import llm

        p = self.profile
        ctx = (
            f"Candidate summary: {p.summary}\n"
            f"Skills: {', '.join(p.skills[:20])}\n"
            f"Years experience: {p.years_experience}\n"
            f"Target roles: {', '.join(p.target_roles)}\n"
            f"Extra notes: {p.description or '(none)'}\n"
        )
        if self.job:
            jd = (self.job.description or "")[:1500]
            ctx += f"\nJob: {self.job.title} at {self.job.company}\n{jd}\n"
        opt_str = f"\nChoose exactly one of these options: {options}" if options else ""
        system = (
            "You are filling out a job application AS the candidate. Answer truthfully "
            "and concisely based ONLY on the provided profile. Never invent credentials "
            "the candidate doesn't have. For free-text, keep it under 120 words. "
            "Return ONLY the answer text, no preamble."
        )
        prompt = f"{ctx}\nApplication question: {question}{opt_str}"
        ans = llm.complete(system, prompt, max_tokens=400).strip()
        if options:  # snap to the closest provided option
            for o in options:
                if o.lower() in ans.lower() or ans.lower() in o.lower():
                    return o
            return options[0] if len(options) == 1 else ans
        return ans
