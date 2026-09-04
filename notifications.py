"""notifications.py - out-of-band approval notifications to Microsoft Teams.

When a high-risk step is waiting on additional approver(s), post a card to a
Teams Incoming Webhook (``TEAMS_WEBHOOK_URL``) with the details and a deep link
back to the pending approval. Best-effort: a no-op when the webhook isn't set,
so local/dev and tests are unaffected.
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Optional


def is_enabled() -> bool:
    return bool(os.getenv("TEAMS_WEBHOOK_URL"))


def notify_pending_approval(
    task_name: str,
    step_name: str,
    have: int,
    needed: int,
    link: Optional[str] = None,
) -> bool:
    """Post an approval card to Teams. Returns True if sent."""
    url = os.getenv("TEAMS_WEBHOOK_URL")
    if not url:
        return False
    link = link or os.getenv("APP_BASE_URL", "")
    card = {
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        "themeColor": "FFB020",
        "summary": f"Approval needed: {task_name}",
        "title": f"Approval needed — {task_name}",
        "sections": [
            {
                "activityTitle": f"Step: {step_name}",
                "facts": [
                    {"name": "Approvals", "value": f"{have} of {needed}"},
                    {"name": "Policy", "value": "Segregation of duties (N-eyes)"},
                ],
                "text": "A different approver is required before this step runs.",
            }
        ],
    }
    if link:
        card["potentialAction"] = [
            {
                "@type": "OpenUri",
                "name": "Review in the harness",
                "targets": [{"os": "default", "uri": link}],
            }
        ]
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(card).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=8)
        return True
    except Exception:
        return False
