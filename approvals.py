"""approvals.py - N-eyes / segregation-of-duties approval policy.

A high-consequence step can require more than one *distinct* human to approve it
before it executes (the classic preparer + approver control). This module holds
the pure policy so it is easy to test; the session layer tracks who has approved.
"""

from __future__ import annotations

import os

from tools import is_high_risk


def default_required() -> int:
    """The configured number of approvers for a high-risk step (>= 1).

    Defaults to 1 (standard single-approver HITL); set REQUIRED_APPROVALS or the
    per-session override to 2+ to enable segregation of duties (N-eyes).
    """
    try:
        return max(1, int(os.getenv("REQUIRED_APPROVALS", "1")))
    except ValueError:
        return 1


def required_approvals(step: dict, high_risk_n: int | None = None) -> int:
    """Distinct approvers a step needs: N when high-risk, else 1."""
    if step and is_high_risk(step):
        return high_risk_n if high_risk_n is not None else default_required()
    return 1


def is_cleared(approvers: set[str], required: int) -> bool:
    """True once enough distinct approvers have signed off."""
    return len(approvers) >= required
