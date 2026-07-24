"""Relevance scoring keeps on-target jobs and drops unrelated ones."""

from __future__ import annotations

import pytest

from job_hunter import relevance
from job_hunter.models import JobStatus, Profile
from job_hunter.resume import analyze


@pytest.fixture
def ml_profile() -> Profile:
    p = Profile()
    analyze.apply_analysis(p, {
        "target_roles": ["Machine Learning Engineer", "Computer Vision Engineer"],
        "search_keywords": ["Machine Learning Engineer", "Computer Vision Intern"],
        "domains": ["Computer Vision", "Deep Learning"],
        "core_competencies": ["PyTorch", "image segmentation", "CNN"],
        "skills": ["Python", "PyTorch", "OpenCV"],
    })
    return p


def test_on_target_scores_high(ml_profile, make_job):
    job = make_job("Computer Vision Engineer")
    job.description = "Build deep learning models with PyTorch for image segmentation."
    assert relevance.score(job, ml_profile) >= 0.5


def test_off_target_scores_low(ml_profile, make_job):
    job = make_job("Sales Development Representative")
    job.description = "Cold-call leads and manage the sales pipeline in Salesforce."
    assert relevance.score(job, ml_profile) < 0.22


def test_annotate_demotes_offtarget(ml_profile, make_job):
    job = make_job("Registered Nurse")
    job.status = JobStatus.eligible
    job.description = "Provide patient care in the ICU."
    relevance.annotate(job, ml_profile)
    assert job.status == JobStatus.ineligible
    assert "relevance" in (job.ineligible_reason or "")


def test_annotate_keeps_ontarget(ml_profile, make_job):
    job = make_job("Machine Learning Engineer")
    job.status = JobStatus.eligible
    job.description = "PyTorch, CNNs, computer vision pipelines."
    relevance.annotate(job, ml_profile)
    assert job.status == JobStatus.eligible
    assert job.match_score and job.match_score >= 0.5


def test_empty_profile_does_not_filter(make_job):
    job = make_job("Anything")
    assert relevance.score(job, Profile()) == 1.0


def test_brief_is_persisted(tmp_home):
    p = Profile()
    analyze.apply_analysis(p, {"brief": "# Jane\nComputer vision engineer with PyTorch."})
    from job_hunter import config
    assert config.BRIEF_PATH.exists()
    assert "Computer vision" in config.BRIEF_PATH.read_text()
