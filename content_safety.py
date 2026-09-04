"""content_safety.py - screen actions through Azure AI Content Safety (#10).

Before the executor performs a side-effecting action, its text is screened by
the Content Safety API on the same AI Services account. Actions flagged at or
above a severity threshold are blocked and handed back to a human - a real,
enforced governance gate rather than a comment.

Auth is AAD (managed identity). Fails open (allows, marks ``checked=False``)
when the service is unavailable so local/offline runs are unaffected.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

_SCOPE = "https://cognitiveservices.azure.com/.default"
_API = "api-version=2024-09-01"
_CATEGORIES = ["Hate", "SelfHarm", "Sexual", "Violence"]
# Severity 0-6; block at/above this. Override via env for stricter policies.
_BLOCK_SEVERITY = int(os.getenv("CONTENT_SAFETY_BLOCK_SEVERITY", "4"))

_LAST_ERROR: Optional[str] = None


def last_error() -> Optional[str]:
    return _LAST_ERROR


def _token() -> Optional[str]:
    try:
        from azure.identity import DefaultAzureCredential

        client_id = os.getenv("AZURE_CLIENT_ID")
        cred = (
            DefaultAzureCredential(managed_identity_client_id=client_id)
            if client_id
            else DefaultAzureCredential()
        )
        return cred.get_token(_SCOPE).token
    except Exception:
        return None


def _endpoint() -> Optional[str]:
    override = os.getenv("CONTENT_SAFETY_ENDPOINT")
    host = None
    if override:
        host = urllib.parse.urlparse(override).netloc or override
    else:
        ep = os.getenv("AZURE_AI_ENDPOINT")
        if ep:
            host = urllib.parse.urlparse(ep).netloc
    if not host:
        return None
    return f"https://{host}/contentsafety/text:analyze?{_API}"


def is_available() -> bool:
    return _endpoint() is not None


def screen(text: str) -> dict:
    """Return a governance verdict for ``text``.

    ``{allowed, checked, max_severity, categories, error}``. Fails open
    (``allowed=True, checked=False``) if the service can't be reached.
    """
    global _LAST_ERROR
    url = _endpoint()
    if not url or not (text or "").strip():
        return {"allowed": True, "checked": False, "max_severity": 0,
                "categories": {}, "error": None}
    token = _token()
    if not token:
        _LAST_ERROR = "no bearer token"
        return {"allowed": True, "checked": False, "max_severity": 0,
                "categories": {}, "error": _LAST_ERROR}
    body = json.dumps(
        {
            "text": text[:10000],
            "categories": _CATEGORIES,
            "outputType": "FourSeverityLevels",
        }
    ).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode(errors="replace")[:200]
        except Exception:
            pass
        _LAST_ERROR = f"HTTP {exc.code}: {detail}"
        return {"allowed": True, "checked": False, "max_severity": 0,
                "categories": {}, "error": _LAST_ERROR}
    except Exception as exc:
        _LAST_ERROR = f"{exc.__class__.__name__}: {str(exc)[:150]}"
        return {"allowed": True, "checked": False, "max_severity": 0,
                "categories": {}, "error": _LAST_ERROR}

    analysis = data.get("categoriesAnalysis", [])
    categories = {c.get("category"): c.get("severity", 0) for c in analysis}
    max_severity = max(categories.values(), default=0)
    _LAST_ERROR = None
    return {
        "allowed": max_severity < _BLOCK_SEVERITY,
        "checked": True,
        "max_severity": max_severity,
        "categories": categories,
        "error": None,
    }
