"""distiller.py - compile approved exemplars into an optimized instruction (#12).

A learned step accumulates several human-approved actions. Distillation asks the
LLM to compress them into ONE concise, generalised instruction (placeholders
preserved), which then becomes the step's portable instruction in ``to_prompt``.
This is the lightweight, dependency-free counterpart to a DSPy-style compile.

No-op offline (no LLM), so tests and local runs are unaffected.
"""

from __future__ import annotations

from skill import Skill


def distill_skill(skill: Skill, min_exemplars: int = 2) -> int:
    """Set ``strat.distilled`` for each mature step. Returns steps updated."""
    from llm_provider import build_chat_model, is_offline

    if is_offline():
        return 0
    llm = build_chat_model(temperature=0.2)
    if llm is None:
        return 0
    from langchain_core.messages import HumanMessage, SystemMessage

    updated = 0
    for strat in skill.strategies.values():
        if strat.approvals < min_exemplars:
            continue
        examples = "\n".join(f"- {e.action}" for e in strat.exemplars[-6:])
        prompt = [
            SystemMessage(
                content="You distill several human-approved actions for one "
                "workflow step into ONE concise, reusable instruction. Keep any "
                "{placeholder} tokens intact. Output only the instruction, one "
                "or two sentences, no preamble."
            ),
            HumanMessage(
                content=f"Step: {strat.step_name}: {strat.step_description}\n"
                f"Approved actions:\n{examples}"
            ),
        ]
        try:
            text = (llm.invoke(prompt).content or "").strip()
        except Exception:
            continue
        if text:
            strat.distilled = text
            updated += 1
    return updated
