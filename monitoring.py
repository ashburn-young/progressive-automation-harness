"""monitoring.py - drift detection over a skill's execution history (#13).

A skill can silently degrade: a tool starts failing, or humans begin rejecting a
step that used to sail through. This computes the recent vs. baseline failure and
rejection rates from the stored traces and flags drift, so a mature automation is
pulled back under supervision before it does damage.

Pure/standalone (no LangGraph/LLM imports) so it is easy to test and call.
"""

from __future__ import annotations

from typing import Any


def _rates(traces: list[dict[str, Any]]) -> dict[str, float]:
    total = len(traces)
    if total == 0:
        return {"total": 0, "failure_rate": 0.0, "rejection_rate": 0.0}
    failed = sum(
        1
        for t in traces
        if t.get("outcome") == "rejected"
        or t.get("execution_status") in ("failed", "blocked")
    )
    rejected = sum(1 for t in traces if t.get("outcome") == "rejected")
    return {
        "total": total,
        "failure_rate": round(failed / total, 3),
        "rejection_rate": round(rejected / total, 3),
    }


def drift_report(
    traces: list[dict[str, Any]],
    window: int = 5,
    threshold: float = 0.34,
) -> dict[str, Any]:
    """Compare the most recent ``window`` traces against the earlier baseline.

    ``drifting`` is True when the recent failure/rejection rate exceeds the
    baseline by ``threshold`` (or is high with no baseline).
    """
    ordered = sorted(traces, key=lambda t: t.get("timestamp", ""))
    recent = ordered[-window:]
    baseline = ordered[:-window]

    recent_rates = _rates(recent)
    baseline_rates = _rates(baseline)

    delta_fail = recent_rates["failure_rate"] - baseline_rates["failure_rate"]
    delta_reject = recent_rates["rejection_rate"] - baseline_rates["rejection_rate"]
    drifting = (
        delta_fail >= threshold
        or delta_reject >= threshold
        or (not baseline and recent_rates["failure_rate"] >= threshold)
    )
    return {
        "total": len(ordered),
        "window": window,
        "recent": recent_rates,
        "baseline": baseline_rates,
        "delta_failure_rate": round(delta_fail, 3),
        "delta_rejection_rate": round(delta_reject, 3),
        "drifting": bool(drifting),
    }
