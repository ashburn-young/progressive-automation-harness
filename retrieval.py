"""retrieval.py - RAG grounding over Azure AI Search (#6).

Gives the harness a knowledge index so each drafted step can be grounded in
organisational context (SOPs, policies, definitions) instead of the model's
priors alone. Documents are embedded with ``text-embedding-3-small`` and stored
in an Azure AI Search vector index; drafting retrieves the top matches and
injects them into the LLM prompt.

Auth is AAD (managed identity) - the search service is private and key-less.
Because the service is reachable only from inside the VNet, the index is built
and seeded from within the deployed app (see ``/api/knowledge/seed``), not from
a dev box. All calls degrade to no-ops when Search/embeddings are unavailable.
"""

from __future__ import annotations

import os
from typing import Optional

import embeddings

_INDEX = "harness-knowledge"
_DIM = 1536  # text-embedding-3-small
_ENDPOINT_DEFAULT = "https://pah-search-qzbtlziciba4a.search.windows.net"

# A small, generic seed corpus so RAG has something to ground on out of the box.
_SEED_DOCS = [
    {
        "id": "sop-stock",
        "source": "Finance SOP",
        "content": (
            "When reporting a stock price, always cite the source and the "
            "timestamp. Use the official ticker symbol and USD unless the "
            "requester specifies another currency."
        ),
    },
    {
        "id": "sop-expense",
        "source": "Expense Policy",
        "content": (
            "Expense reports must group receipts by cost center. Any single "
            "expense over 1000 USD requires manager approval before submission "
            "to the finance system."
        ),
    },
    {
        "id": "sop-approval",
        "source": "Controls Policy",
        "content": (
            "Irreversible actions - submitting, sending, paying, transferring - "
            "always require a human reviewer, even for a mature automation."
        ),
    },
    {
        "id": "sop-browsing",
        "source": "Research Guide",
        "content": (
            "When researching online, prefer primary sources, capture the URL, "
            "and summarise findings in one short paragraph."
        ),
    },
]


def _endpoint() -> str:
    return os.getenv("SEARCH_ENDPOINT", _ENDPOINT_DEFAULT)


def _credential():
    from azure.identity import DefaultAzureCredential

    client_id = os.getenv("AZURE_CLIENT_ID")
    return (
        DefaultAzureCredential(managed_identity_client_id=client_id)
        if client_id
        else DefaultAzureCredential()
    )


def is_available() -> bool:
    return bool(_endpoint()) and embeddings.is_available()


def ensure_index() -> bool:
    """Create the vector index if it doesn't exist. Returns True on success."""
    from azure.search.documents.indexes import SearchIndexClient
    from azure.search.documents.indexes.models import (
        HnswAlgorithmConfiguration,
        SearchField,
        SearchFieldDataType,
        SearchIndex,
        SimpleField,
        VectorSearch,
        VectorSearchProfile,
    )

    client = SearchIndexClient(_endpoint(), _credential())
    fields = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True),
        SearchField(
            name="content",
            type=SearchFieldDataType.String,
            searchable=True,
        ),
        SimpleField(name="source", type=SearchFieldDataType.String, filterable=True),
        SearchField(
            name="contentVector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=_DIM,
            vector_search_profile_name="default",
        ),
    ]
    vector_search = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name="default-hnsw")],
        profiles=[
            VectorSearchProfile(
                name="default", algorithm_configuration_name="default-hnsw"
            )
        ],
    )
    index = SearchIndex(name=_INDEX, fields=fields, vector_search=vector_search)
    client.create_or_update_index(index)
    return True


def seed_default_knowledge() -> dict:
    """Embed and upload the seed corpus. Returns a diagnostic result dict."""
    try:
        ensure_index()
    except Exception as exc:
        return {"seeded": 0, "stage": "ensure_index", "error": _err(exc)}
    vectors = embeddings.embed([d["content"] for d in _SEED_DOCS])
    if not vectors:
        return {
            "seeded": 0,
            "stage": "embed",
            "error": embeddings.last_error() or "no embeddings returned",
        }
    try:
        from azure.search.documents import SearchClient

        client = SearchClient(_endpoint(), _INDEX, _credential())
        docs = [
            {**doc, "contentVector": vector}
            for doc, vector in zip(_SEED_DOCS, vectors)
        ]
        client.upload_documents(documents=docs)
        return {"seeded": len(docs), "stage": "done", "error": None}
    except Exception as exc:
        return {"seeded": 0, "stage": "upload", "error": _err(exc)}


def _err(exc: Exception) -> str:
    return f"{exc.__class__.__name__}: {str(exc)[:300]}"


def retrieve(query: str, k: int = 3) -> list[str]:
    """Return up to ``k`` grounding snippets for ``query`` (empty on any failure)."""
    if not query or not is_available():
        return []
    vector = embeddings.embed_one(query)
    if not vector:
        return []
    try:
        from azure.search.documents import SearchClient
        from azure.search.documents.models import VectorizedQuery
    except Exception:
        return []
    client = SearchClient(_endpoint(), _INDEX, _credential())
    vq = VectorizedQuery(
        vector=vector, k_nearest_neighbors=k, fields="contentVector"
    )
    try:
        results = client.search(search_text=None, vector_queries=[vq], top=k)
        return [r["content"] for r in results if r.get("content")]
    except Exception:
        return []
