"""Provider-agnostic LLM helper for the standalone brain.

Supports Anthropic and OpenAI interchangeably — the provider is chosen from the
environment (see `config.llm_provider`). Two jobs only:
  1. structured extraction (resume -> role profile), and
  2. free-text judgement (answer an application question as the user).

When the tool is driven through the MCP server, Claude Code performs these
reasoning steps itself and this module is never touched — so missing API keys are
only fatal for fully-standalone autonomous runs.
"""

from __future__ import annotations

import json
from typing import Any

from . import config


class LLMUnavailable(RuntimeError):
    pass


# ---- provider backends ----------------------------------------------------

def _anthropic_complete(model: str, system: str, user: str, max_tokens: int) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=config.anthropic_key())
    msg = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")


def _openai_complete(model: str, system: str, user: str, max_tokens: int) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=config.openai_key())
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    # Newer models (gpt-5, o-series) require `max_completion_tokens`; older ones
    # (gpt-4o, gpt-4) accept it too. Fall back to `max_tokens` only if rejected.
    try:
        resp = client.chat.completions.create(
            model=model, messages=messages, max_completion_tokens=max_tokens,
        )
    except Exception as e:  # noqa: BLE001
        if "max_completion_tokens" not in str(e) and "max_tokens" not in str(e):
            raise
        resp = client.chat.completions.create(
            model=model, messages=messages, max_tokens=max_tokens,
        )
    return resp.choices[0].message.content or ""


# ---- public API -----------------------------------------------------------

def complete(system: str, user: str, max_tokens: int = 2000) -> str:
    provider = config.llm_provider()
    model = config.model_for(provider)

    if provider == "anthropic":
        if not config.anthropic_key():
            raise LLMUnavailable(_no_key_msg("ANTHROPIC_API_KEY"))
        return _anthropic_complete(model, system, user, max_tokens)
    if provider == "openai":
        if not config.openai_key():
            raise LLMUnavailable(_no_key_msg("OPENAI_API_KEY"))
        return _openai_complete(model, system, user, max_tokens)
    raise LLMUnavailable(f"Unknown JOBHUNTER_PROVIDER: {provider!r} (use 'anthropic' or 'openai').")


def complete_json(system: str, user: str, max_tokens: int = 2000) -> dict[str, Any]:
    """Complete and parse a JSON object, tolerating markdown fences and prose."""
    raw = complete(system + "\n\nRespond with ONLY a valid JSON object.", user, max_tokens).strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        raw = raw.removeprefix("json")
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end != -1:
        raw = raw[start : end + 1]
    return json.loads(raw)


def _no_key_msg(var: str) -> str:
    return (
        f"{var} is not set. Either export it, choose the other provider via "
        "JOBHUNTER_PROVIDER, or drive the tool through Claude Code via the MCP "
        "server (which needs no key)."
    )
