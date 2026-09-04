"""embeddings.py - text embeddings via the Foundry endpoint (AAD, no keys).

The Foundry v1 endpoint is OpenAI-compatible, so embeddings are a POST to
``{AZURE_AI_ENDPOINT}/embeddings`` with the ``text-embedding-3-small``
deployment. Shared by RAG retrieval (#6) and embeddings-based tool binding (#9).

Everything degrades to ``None``/empty when the endpoint or auth is unavailable,
so local/offline runs and tests are unaffected.
"""

from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from llm_provider import _azure_bearer_token

_DEPLOYMENT = os.getenv("AZURE_AI_EMBED_DEPLOYMENT", "text-embedding-3-small")

# Last embedding error, for diagnostics (never contains secrets).
_LAST_ERROR: Optional[str] = None


def last_error() -> Optional[str]:
    return _LAST_ERROR


def is_available() -> bool:
    return bool(os.getenv("AZURE_AI_ENDPOINT")) and _azure_bearer_token() is not None


def _embeddings_url() -> Optional[str]:
    """The account-level OpenAI v1 embeddings URL.

    The project endpoint (AZURE_AI_ENDPOINT, .../projects/<p>/openai/v1) serves
    chat but not embeddings, so we target the account-level v1 route on the same
    host: https://<host>/openai/v1/embeddings.
    """
    override = os.getenv("AZURE_AI_EMBED_ENDPOINT")
    if override:
        return override.rstrip("/") + "/embeddings"
    endpoint = os.getenv("AZURE_AI_ENDPOINT")
    if not endpoint:
        return None
    host = urllib.parse.urlparse(endpoint).netloc
    if not host:
        return None
    return f"https://{host}/openai/v1/embeddings"


def embed(texts: list[str]) -> Optional[list[list[float]]]:
    """Return an embedding vector per input text, or ``None`` on any failure."""
    global _LAST_ERROR
    url = _embeddings_url()
    if not url or not texts:
        _LAST_ERROR = "no endpoint or empty input"
        return None
    token = _azure_bearer_token()
    if not token:
        _LAST_ERROR = "no bearer token"
        return None
    body = json.dumps({"model": _DEPLOYMENT, "input": texts}).encode()
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
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
        _LAST_ERROR = None
        return [row["embedding"] for row in data["data"]]
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode(errors="replace")[:300]
        except Exception:
            pass
        _LAST_ERROR = f"HTTP {exc.code} at {url}: {detail}"
        return None
    except Exception as exc:
        _LAST_ERROR = f"{exc.__class__.__name__}: {str(exc)[:200]} (url={url})"
        return None


def embed_one(text: str) -> Optional[list[float]]:
    vectors = embed([text])
    return vectors[0] if vectors else None


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length vectors (0.0 if degenerate)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
