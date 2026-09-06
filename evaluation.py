"""evaluation.py — a multi-dimensional quality scorecard for a Skill.

Inspired by SkillNet's five-dimension evaluation (Safety, Completeness,
Executability, Maintainability, Cost-awareness). Every score is derived from
signals the harness already records — approvals, failures, rejections, maturity,
distilled instructions, and execution traces — so the scorecard reflects real
evidence, not a model's opinion. Scores are 0-100 per dimension plus an overall.
"""

from __future__ import annotations

from typing import Any, Optional


def _pct(numerator: float, denominator: float) -> int:
    if denominator <= 0:
        return 100
    return max(0, min(100, round(100 * numerator / denominator)))


def evaluate_skill(skill, traces: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    """Return a five-dimension quality scorecard grounded in recorded signals."""
    traces = traces or []
    rows = skill.report_rows()
    total = len(rows) or 1
    approvals = sum(int(r["approvals"]) for r in rows)
    failures = sum(int(r["failures"]) for r in rows)
    rejections = sum(int(r["rejections"]) for r in rows)
    mature = sum(1 for r in rows if r["maturity"] in ("deterministic", "autonomous"))
    covered = sum(1 for r in rows if int(r["approvals"]) > 0)
    distilled = sum(1 for r in rows if r["distilled"])

    exec_events = [t for t in traces if t.get("execution_status")]
    blocked = sum(1 for t in exec_events if t["execution_status"] == "blocked")
    failed = sum(1 for t in exec_events if t["execution_status"] == "failed")
    total_exec = len(exec_events)

    # 1. Safety — no content-safety-blocked executions; contested actions cost.
    safety = _pct(total_exec - blocked, total_exec) if total_exec else 100
    if rejections:
        safety = max(0, safety - min(20, rejections * 5))

    # 2. Completeness — every step has an approved exemplar.
    completeness = _pct(covered, total)

    # 3. Executability — approved actions run without failing.
    if total_exec:
        executability = _pct(total_exec - failed - blocked, total_exec)
    else:
        denom = approvals + failures
        executability = _pct(approvals, denom) if denom else (60 if approvals else 30)

    # 4. Maintainability — documented and distilled into reusable instructions.
    maint = (30 if (skill.summary or "").strip() else 0)
    maint += round(40 * distilled / total)
    maint += 30 if skill.required_inputs else 15
    maintainability = max(0, min(100, maint))

    # 5. Cost-awareness — mastered steps replay with no LLM call.
    cost = _pct(mature, total)

    dimensions = [
        {"key": "safety", "label": "Safety", "score": safety,
         "detail": (f"{blocked} blocked execution(s)" if total_exec else "high-risk steps human-gated")},
        {"key": "completeness", "label": "Completeness", "score": completeness,
         "detail": f"{covered}/{total} steps have approved exemplars"},
        {"key": "executability", "label": "Executability", "score": executability,
         "detail": (f"{total_exec - failed - blocked}/{total_exec} runs succeeded"
                    if total_exec else f"{failures} recorded failure(s)")},
        {"key": "maintainability", "label": "Maintainability", "score": maintainability,
         "detail": f"{distilled}/{total} steps distilled" + ("" if (skill.summary or "").strip() else "; no summary")},
        {"key": "cost", "label": "Cost-awareness", "score": cost,
         "detail": f"{mature}/{total} steps replay with no LLM call"},
    ]
    overall = round(sum(d["score"] for d in dimensions) / len(dimensions))
    weakest = min(d["score"] for d in dimensions)
    grade = "A" if overall >= 85 else "B" if overall >= 70 else "C" if overall >= 55 else "D"
    return {"overall": overall, "weakest": weakest, "grade": grade, "dimensions": dimensions}
