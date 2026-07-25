"""Guardrails: document allowlist + consent, official-site verify, NL selection."""

from __future__ import annotations

from job_hunter import documents, service, store
from job_hunter.apply import verify
from job_hunter.models import Profile

# ---- document allowlist ---------------------------------------------------

def test_only_registered_files_allowed(tmp_path):
    p = Profile()
    resume = tmp_path / "resume.pdf"
    resume.write_text("x")
    p.identity.resume_path = str(resume)
    other = tmp_path / "secret.pdf"
    other.write_text("y")
    assert documents.is_allowed(p, str(resume))
    assert not documents.is_allowed(p, str(other))     # never upload un-registered


def test_register_missing_file_fails(tmp_path):
    p = Profile()
    assert documents.register(p, "cl", str(tmp_path / "nope.pdf")) is False


def test_sensitive_doc_requires_consent(tmp_path):
    p = Profile()
    resume = tmp_path / "resume.pdf"
    resume.write_text("x")
    p.identity.resume_path = str(resume)
    # A field asking for Aadhaar must NOT auto-use the resume — it asks for consent.
    path, consent = documents.resolve_upload(p, "Upload your Aadhaar card")
    assert path is None and consent and "Aadhaar" in consent


def test_resume_used_for_cv_field(tmp_path):
    p = Profile()
    resume = tmp_path / "resume.pdf"
    resume.write_text("x")
    p.identity.resume_path = str(resume)
    path, consent = documents.resolve_upload(p, "Attach your CV / resume")
    assert consent is None and path == str(resume)


# ---- official-site verification ------------------------------------------

def test_trusted_ats_passes():
    ok, _ = verify.check("https://boards.greenhouse.io/acme/jobs/123", "Acme")
    assert ok


def test_company_domain_passes():
    ok, _ = verify.check("https://careers.acme.com/apply", "Acme Technologies")
    assert ok


def test_unrelated_domain_blocked():
    ok, why = verify.check("https://totally-not-acme-phishing.xyz/apply", "Acme")
    assert not ok and "unverified" in why


# ---- plain-English selection (no LLM fallback) ---------------------------

def test_select_jobs_text_match(make_job, monkeypatch):
    from job_hunter import config
    monkeypatch.setattr(config, "has_llm", lambda: False)
    a = make_job("ML Engineer A", company="Microsoft")
    a.rag = "green"
    b = make_job("ML Engineer B", company="Acme")
    b.rag = "green"
    store.upsert_job(a)
    store.upsert_job(b)
    res = service.select_jobs_nl("apply to the microsoft roles", use_llm=False)
    assert a.id in res["job_ids"] and b.id not in res["job_ids"]
