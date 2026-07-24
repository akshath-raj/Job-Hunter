"""Generic web-form and Google-Form filling for external (non-Easy-Apply) jobs.

This is best-effort and intentionally conservative: it fills what it can map with
confidence, uploads the resume where a file input exists, and if a required field
can't be answered it stops and reports `needs_input` rather than guessing on a
real submission. Truly novel sites are where the MCP path shines — Claude Code
drives Playwright directly with full page context.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..models import Profile
from .answerer import ProfileAnswerer


@dataclass
class FormResult:
    submitted: bool = False
    needs_input: bool = False
    prompt: str | None = None
    answers: dict[str, str] = field(default_factory=dict)
    error: str | None = None


async def _label_for(page, control) -> str:
    cid = await control.get_attribute("id")
    if cid:
        lab = await page.query_selector(f"label[for='{cid}']")
        if lab:
            t = (await lab.inner_text()).strip()
            if t:
                return t
    aria = await control.get_attribute("aria-labelledby")
    if aria:
        el = await page.query_selector(f"#{aria}")
        if el:
            return (await el.inner_text()).strip()
    for attr in ("aria-label", "name", "placeholder"):
        v = await control.get_attribute(attr)
        if v:
            return v.strip()
    return "field"


async def fill_generic_form(page, profile: Profile, answerer: ProfileAnswerer,
                            resume_path: str | None) -> FormResult:
    result = FormResult()

    # Text-like inputs + textareas.
    for control in await page.query_selector_all(
        "input[type='text'], input[type='email'], input[type='tel'], "
        "input[type='number'], input[type='url'], textarea"
    ):
        if not await control.is_visible():
            continue
        if (await control.input_value()).strip():
            continue
        label = await _label_for(page, control)
        ans = await answerer(label, None)
        if ans:
            await control.fill(ans)
            result.answers[label] = ans
        elif await control.get_attribute("required") is not None:
            result.needs_input = True

    # Resume upload.
    if resume_path:
        for fileinput in await page.query_selector_all("input[type='file']"):
            try:
                await fileinput.set_input_files(resume_path)
                result.answers["resume"] = resume_path
            except Exception:  # noqa: BLE001
                pass

    # Selects.
    for sel in await page.query_selector_all("select"):
        if not await sel.is_visible():
            continue
        options = [(await o.inner_text()).strip() for o in await sel.query_selector_all("option")]
        options = [o for o in options if o]
        label = await _label_for(page, sel)
        ans = await answerer(label, options)
        if ans:
            try:
                await sel.select_option(label=ans)
                result.answers[label] = ans
            except Exception:  # noqa: BLE001
                result.needs_input = True

    if result.needs_input:
        result.prompt = "External form has required fields I couldn't fill confidently."
    return result


async def submit_google_form(page, profile: Profile, answerer: ProfileAnswerer) -> FormResult:
    """Google Forms have a consistent structure: list items each with a question."""
    result = FormResult()
    items = await page.query_selector_all("div[role='listitem']")
    for item in items:
        heading = await item.query_selector("div[role='heading']")
        if not heading:
            continue
        question = (await heading.inner_text()).strip()

        text_in = await item.query_selector("input[type='text'], textarea")
        if text_in:
            ans = await answerer(question, None)
            if ans:
                await text_in.fill(ans)
                result.answers[question] = ans
            continue

        radios = await item.query_selector_all("div[role='radio']")
        if radios:
            options = [(await r.get_attribute("aria-label")) or "" for r in radios]
            ans = await answerer(question, options)
            for r, opt in zip(radios, options, strict=False):
                if ans and ans.lower() in opt.lower():
                    await r.click()
                    result.answers[question] = opt
                    break

    submit = await page.query_selector("div[role='button']:has-text('Submit')")
    if submit:
        await submit.click()
        result.submitted = True
    else:
        result.needs_input = True
        result.prompt = "Could not find the Google Form submit button."
    return result
