"""llm_provider.py — Central chat-model factory.

Targets an Azure AI Foundry project endpoint using Azure AD auth (no API keys).
Set the endpoint/deployment via environment variables (see ``.env.example``):

    AZURE_AI_ENDPOINT   e.g. https://<res>.services.ai.azure.com/api/projects/<proj>/openai/v1
    AZURE_AI_DEPLOYMENT e.g. gpt-4o-mini

Falls back to plain OpenAI if ``OPENAI_API_KEY`` is set, and to ``None``
(offline / mock mode) when neither is configured or auth fails.
"""

from __future__ import annotations

import os
from typing import Optional

# Azure AD data-plane scope for Azure AI Foundry project endpoints.
_AAD_SCOPE = "https://ai.azure.com/.default"

# Cache the resolved provider decision so ``is_offline`` and ``build_chat_model``
# agree within a single process.
_offline_cache: Optional[bool] = None


def _azure_bearer_token() -> Optional[str]:
    """Fetch an AAD bearer token for the logged-in identity, or None on failure."""
    try:
        from azure.identity import DefaultAzureCredential

        cred = DefaultAzureCredential()
        return cred.get_token(_AAD_SCOPE).token
    except Exception:
        return None


def default_model() -> str:
    """The configured default chat deployment."""
    return os.getenv("AZURE_AI_DEPLOYMENT", "gpt-4o-mini")


def available_models() -> list[str]:
    """Selectable chat deployments (default first). Override via AZURE_AI_MODELS."""
    env = os.getenv("AZURE_AI_MODELS")
    if env:
        models = [m.strip() for m in env.split(",") if m.strip()]
    else:
        models = [default_model(), "gpt-5.6-luna", "gpt-5.4-mini", "gpt-5.4-nano"]
    # De-dupe, keep order, ensure the default is first.
    seen: set[str] = set()
    ordered = [default_model()] + [m for m in models if m != default_model()]
    return [m for m in ordered if not (m in seen or seen.add(m))]


# "auto" tiers a model to the requested granularity: cheap/fast for concise,
# the default for balanced, a stronger model for detailed.
_AUTO_TIERS = {
    "concise": "gpt-5.4-nano",
    "balanced": None,  # use the default
    "detailed": "gpt-5.6-luna",
}


def resolve_model(requested: Optional[str], detail: str = "balanced") -> str:
    """Pick the deployment for a request: an explicit choice, or an auto tier."""
    if requested and requested.lower() != "auto":
        chosen = requested
    else:
        chosen = _AUTO_TIERS.get(detail) or default_model()
    return chosen if chosen in available_models() else default_model()


def build_chat_model(temperature: float = 0.2, deployment: Optional[str] = None):
    """Return a LangChain chat model, or ``None`` when running offline.

    ``deployment`` overrides the default model; provider precedence is
    Azure AI Foundry endpoint -> OpenAI -> offline.
    """
    global _offline_cache

    endpoint = os.getenv("AZURE_AI_ENDPOINT")
    model_name = deployment or default_model()

    if endpoint:
        token = _azure_bearer_token()
        if token:
            from langchain_openai import ChatOpenAI

            _offline_cache = False
            # The Foundry v1 endpoint is OpenAI-compatible; the AAD token is
            # passed as the bearer credential. GPT-5-class deployments only
            # accept the default temperature (1), so we don't override it.
            return ChatOpenAI(
                model=model_name,
                base_url=endpoint,
                api_key=token,
                temperature=1,
            )

    if os.getenv("OPENAI_API_KEY"):
        from langchain_openai import ChatOpenAI

        _offline_cache = False
        return ChatOpenAI(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            temperature=temperature,
        )

    _offline_cache = True
    return None


def is_offline() -> bool:
    """True when no live LLM is available (drafting falls back to mocks)."""
    if _offline_cache is None:
        # Resolve lazily without holding onto a model instance.
        build_chat_model()
    return bool(_offline_cache)
