"""skill_compiler.py — Harvest approved traces into a Skill.

This is the concrete, working stand-in for the "DSPy compilation / deterministic
hardening" step. It rebuilds a :class:`Skill` from the append-only training log
so the operation is idempotent: every approved trace for a task becomes an
exemplar on the matching step strategy, and maturity is derived from how
consistently those approvals repeat.

Kept free of LangGraph/LangChain imports so it can run and be tested standalone.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Iterator, Optional

from skill import Skill, slugify, templatize


def _iter_traces(log_path: Path) -> Iterator[dict]:
    if not log_path.exists():
        return
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def compile_skill_from_logs(
    task_name: str,
    log_path: Path,
    skills_dir: Path,
    *,
    summary: str = "",
    required_inputs: Optional[list[str]] = None,
    save: bool = True,
) -> Skill:
    """Rebuild the skill for ``task_name`` from all approved traces in the log.

    Returns the compiled :class:`Skill`. When ``save`` is True it is also written
    to ``skills_dir/<slug>.json``.
    """
    skill = compile_from_records(
        task_name,
        _iter_traces(log_path),
        summary=summary,
        required_inputs=required_inputs,
    )
    if save:
        skill.save(skills_dir)
    return skill


def compile_from_records(
    task_name: str,
    records: Iterable[dict],
    *,
    summary: str = "",
    required_inputs: Optional[list[str]] = None,
) -> Skill:
    """Build (without saving) a :class:`Skill` from an iterable of trace records.

    Shared by the file-based and Cosmos-backed stores so both compile identically.
    """
    skill = Skill(
        task_name=task_name,
        slug=slugify(task_name),
        summary=summary,
        required_inputs=list(required_inputs or []),
    )

    for trace in records:
        if trace.get("task_name") != task_name:
            continue
        req = trace.get("original_request", {})
        step_id = req.get("step_id")
        if step_id is None:
            continue
        strat = skill.ensure_strategy(
            step_id=step_id,
            step_name=req.get("step_name", f"step-{step_id}"),
            step_description=req.get("step_description", ""),
        )
        # Enrich skill-level metadata from the first trace that carries it.
        if not skill.required_inputs and req.get("required_inputs"):
            skill.required_inputs = list(req["required_inputs"])
        if trace.get("outcome") == "rejected":
            strat.rejections += 1
        else:
            # Store a templated exemplar so the skill generalises across inputs.
            strat.add_approval(
                action=templatize(
                    trace.get("drafted_action", ""), trace.get("inputs")
                ),
                feedback=trace.get("human_feedback", ""),
            )
            # A failed or safety-blocked execution caps autonomy even when the
            # draft was approved.
            if trace.get("execution_status") in ("failed", "blocked"):
                strat.failures += 1

    return skill
