"""Excel export, enrichment merge, and cheap-model routing."""

from __future__ import annotations

from openpyxl import load_workbook

from job_hunter import enrich, export
from job_hunter.agents import Complexity, pick_model


def test_pick_model_is_cheap_first():
    assert "haiku" in pick_model(Complexity.trivial)
    assert "haiku" in pick_model("simple")
    assert "sonnet" in pick_model(Complexity.complex)


def test_apply_enrichment_sets_fields(make_job):
    job = make_job("Backend Engineer")
    enrich.apply_enrichment(job, {
        "about": "Makes widgets.", "salary": "$120k-$150k",
        "qualifications": "3+ yrs Python", "source": "job posting",
    })
    assert job.enriched is True
    assert job.salary == "$120k-$150k"


def test_enrichment_prompt_mentions_json(make_job):
    prompt = enrich.enrichment_prompt(make_job("Data Scientist", company="Acme"))
    assert "Acme" in prompt and "JSON" in prompt


def test_to_excel_writes_rows(make_job, tmp_path):
    jobs = [make_job("Backend Engineer"), make_job("Data Scientist", company="Beta")]
    jobs[0].salary = "$120k"
    out = export.to_excel(jobs, tmp_path / "jobs.xlsx")
    wb = load_workbook(out)
    ws = wb.active
    assert ws.cell(row=1, column=1).value == "ID"          # header
    assert ws.max_row == 3                                   # header + 2 jobs
    values = [c.value for c in ws[2]]
    assert "Backend Engineer" in values
