"""Deterministic (no-LLM) answering pulls the right values from the profile."""

from __future__ import annotations

import pytest

from job_hunter.apply.answerer import ProfileAnswerer
from job_hunter.models import Profile


@pytest.fixture
def answerer() -> ProfileAnswerer:
    p = Profile()
    p.identity.full_name = "Jane Doe"
    p.identity.email = "jane@example.com"
    p.identity.phone = "+1-555-0100"
    p.identity.location = "New York, NY"
    p.identity.linkedin_url = "https://linkedin.com/in/janedoe"
    p.years_experience = 3.0
    p.constraints.work_authorization = "US Citizen"
    p.constraints.require_sponsorship = False
    return ProfileAnswerer(p, use_llm=False)


@pytest.mark.parametrize("question,options,expected", [
    ("Email address", None, "jane@example.com"),
    ("Mobile phone", None, "+1-555-0100"),
    ("First name", None, "Jane"),
    ("Last name", None, "Doe"),
    ("Current city", None, "New York, NY"),
    ("LinkedIn profile URL", None, "https://linkedin.com/in/janedoe"),
    ("Years of experience", None, "3"),
    ("Are you legally authorized to work?", ["Yes", "No"], "Yes"),
    ("Do you now or will you require sponsorship?", ["Yes", "No"], "No"),
])
async def test_deterministic_answers(answerer, question, options, expected):
    assert await answerer(question, options) == expected


async def test_free_text_authorization_returns_detail(answerer):
    assert await answerer("Describe your work authorization", None) == "US Citizen"


async def test_injected_override_wins(answerer):
    answerer.inject("Why do you want this role?", "Because I love backend systems.")
    assert await answerer("Why do you want this role?", None) == "Because I love backend systems."


async def test_unknown_without_llm_returns_blank(answerer):
    assert await answerer("What is your favorite color?", None) == ""
