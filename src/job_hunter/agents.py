"""Dynamic, cost-aware sub-agents.

Two ideas:
  1. `pick_model(complexity)` — route work to the cheapest capable model. Trivial
     lookups -> Haiku; anything needing reasoning/synthesis -> Sonnet. Callers
     never hard-code a model, so the whole app stays cheap by default.
  2. `research(task, complexity)` — spawn a short-lived agent (via the Claude
     Agent SDK, which can use web search) to answer a question, e.g. "what's the
     typical salary for this role at this company?". Falls back to a plain LLM
     completion when the SDK/web isn't available.

When Job Hunter runs *inside* Claude Code (MCP mode), Claude is the orchestrator
and should spawn these as real subagents with the Task tool at the model
`pick_model` recommends — this module is the standalone equivalent.
"""

from __future__ import annotations

from enum import StrEnum


class Complexity(StrEnum):
    trivial = "trivial"    # extraction, formatting, yes/no
    simple = "simple"      # short lookup, light judgement
    complex = "complex"    # multi-step reasoning, synthesis, web research


# Cheap-first model routing. Override via env if desired.
MODEL_BY_COMPLEXITY = {
    Complexity.trivial: "claude-haiku-4-5",
    Complexity.simple: "claude-haiku-4-5",
    Complexity.complex: "claude-sonnet-4-6",
}


def pick_model(complexity: Complexity | str) -> str:
    c = Complexity(complexity) if not isinstance(complexity, Complexity) else complexity
    return MODEL_BY_COMPLEXITY[c]


async def research(task: str, complexity: Complexity | str = Complexity.complex,
                   system: str | None = None, max_turns: int = 4) -> str:
    """Run a one-shot research agent and return its text answer.

    Prefers the Claude Agent SDK (can browse the web). Degrades to a plain
    provider completion if the SDK isn't usable in this environment.
    """
    model = pick_model(complexity)
    sys = system or (
        "You are a concise research assistant. Use web search when helpful. "
        "Return only the requested facts, no preamble."
    )
    try:
        return await _sdk_research(task, model, sys, max_turns)
    except Exception:  # noqa: BLE001 — SDK unavailable/unauthed -> fallback
        from . import llm

        try:
            return llm.complete(sys, task, max_tokens=600)
        except Exception:  # noqa: BLE001
            return ""


async def _sdk_research(task: str, model: str, system: str, max_turns: int) -> str:
    from claude_agent_sdk import ClaudeAgentOptions, query

    options = ClaudeAgentOptions(
        model=model,
        system_prompt=system,
        allowed_tools=["WebSearch", "WebFetch"],
        max_turns=max_turns,
    )
    chunks: list[str] = []
    async for message in query(prompt=task, options=options):
        # Collect assistant text blocks across message shapes the SDK may emit.
        content = getattr(message, "content", None)
        if isinstance(content, str):
            chunks.append(content)
        elif isinstance(content, list):
            for block in content:
                text = getattr(block, "text", None)
                if text:
                    chunks.append(text)
    return "\n".join(chunks).strip()
