"""LinkedIn Easy Apply automation.

Easy Apply is a multi-step modal. We loop: read the current step's fields, answer
each one, click Next, repeat until we reach Review -> Submit. Answers come from a
pluggable `answerer` (deterministic from the profile first, LLM/Claude for the
rest) so the same flow works autonomously or human-guided.

`apply()` returns an ApplyResult describing what happened, including whether it
paused for input (e.g. a required question nobody could answer, or a CAPTCHA).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from .browser import human_pause

# answerer(question_label, options_or_None) -> answer string ("" to skip/leave blank)
Answerer = Callable[[str, list[str] | None], Awaitable[str]]


@dataclass
class ApplyResult:
    submitted: bool = False
    needs_input: bool = False
    prompt: str | None = None            # what to ask the user, if paused
    answers: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    steps: int = 0


async def _label_for(page, control) -> str:
    """Best-effort human label for a form control."""
    cid = await control.get_attribute("id")
    if cid:
        lab = await page.query_selector(f"label[for='{cid}']")
        if lab:
            t = (await lab.inner_text()).strip()
            if t:
                return t
    for attr in ("aria-label", "name", "placeholder"):
        v = await control.get_attribute(attr)
        if v:
            return v.strip()
    return "unknown field"


async def _fill_step(page, answerer: Answerer, answers: dict[str, str]) -> bool:
    """Answer every control in the current modal step.

    Returns False if a required field was left unanswered (=> we must pause).
    """
    ok = True

    # Text / email / tel / number inputs and textareas.
    for control in await page.query_selector_all(
        ".jobs-easy-apply-content input[type='text'], "
        ".jobs-easy-apply-content input[type='email'], "
        ".jobs-easy-apply-content input[type='tel'], "
        ".jobs-easy-apply-content input[type='number'], "
        ".jobs-easy-apply-content textarea"
    ):
        current = await control.input_value()
        if current.strip():
            continue  # LinkedIn pre-filled it
        label = await _label_for(page, control)
        ans = await answerer(label, None)
        if ans:
            await control.click()
            await control.fill(ans)
            answers[label] = ans
            await human_pause(0.3, 0.9)
        elif await control.get_attribute("required") is not None:
            ok = False

    # Native <select> dropdowns.
    for sel in await page.query_selector_all(".jobs-easy-apply-content select"):
        label = await _label_for(page, sel)
        options = [
            (await o.inner_text()).strip()
            for o in await sel.query_selector_all("option")
            if (await o.get_attribute("value"))
        ]
        ans = await answerer(label, options)
        if ans:
            try:
                await sel.select_option(label=ans)
                answers[label] = ans
            except Exception:  # noqa: BLE001
                ok = False
        elif options:
            ok = False

    # Radio groups (Yes/No, sponsorship, etc.).
    for group in await page.query_selector_all(
        ".jobs-easy-apply-content fieldset[data-test-form-builder-radio-button-form-component]"
    ):
        legend = await group.query_selector("legend")
        label = (await legend.inner_text()).strip() if legend else "choice"
        radios = await group.query_selector_all("input[type='radio']")
        options = []
        for r in radios:
            rid = await r.get_attribute("id")
            lab = await group.query_selector(f"label[for='{rid}']") if rid else None
            if lab:
                options.append((await lab.inner_text()).strip())
            else:
                options.append(await r.get_attribute("value") or "")
        ans = await answerer(label, options)
        chosen = False
        for r, opt in zip(radios, options, strict=False):
            if ans and ans.lower() in opt.lower():
                await r.check()
                answers[label] = opt
                chosen = True
                break
        if not chosen and radios:
            ok = False

    return ok


async def _click(page, texts: list[str]):
    for t in texts:
        btn = await page.query_selector(f"button[aria-label*='{t}'], button:has-text('{t}')")
        if btn and await btn.is_enabled():
            return btn
    return None


async def apply(page, job_url: str, answerer: Answerer, max_steps: int = 12) -> ApplyResult:
    result = ApplyResult()

    await page.goto(job_url, wait_until="domcontentloaded")
    await human_pause(1.0, 2.0)

    start = await page.query_selector("button.jobs-apply-button")
    if not start:
        result.error = "No apply button found"
        return result
    label = (await start.inner_text()).strip().lower()
    if "easy apply" not in label:
        result.error = "Not an Easy Apply job (external application)"
        return result
    await start.click()
    await human_pause(1.0, 2.0)

    for _ in range(max_steps):
        result.steps += 1
        # CAPTCHA / security check guard.
        if await page.query_selector("iframe[title*='captcha'], iframe[src*='captcha']"):
            result.needs_input = True
            result.prompt = "A CAPTCHA appeared during Easy Apply. Please solve it in the browser."
            return result

        filled_ok = await _fill_step(page, answerer, result.answers)
        if not filled_ok:
            result.needs_input = True
            result.prompt = (
                "This application has a required question I couldn't answer confidently. "
                "Please complete it in the browser window, or provide the answer."
            )
            return result

        submit = await _click(page, ["Submit application"])
        if submit:
            await submit.click()
            await human_pause(1.0, 2.0)
            result.submitted = True
            return result

        nxt = await _click(page, ["Review", "Next", "Continue to next step"])
        if not nxt:
            result.error = "Could not find Next/Review/Submit — layout may have changed."
            return result
        await nxt.click()
        await human_pause(0.8, 1.6)

    result.error = "Exceeded max steps without submitting."
    return result
