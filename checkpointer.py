"""checkpointer.py - durable LangGraph checkpointing.

LangGraph's default ``InMemorySaver`` loses all HITL sessions when the process
restarts or when traffic lands on a different replica. This module adds a
Cosmos-backed saver so a paused co-pilot session (waiting for a human to
approve/reject a step) survives restarts and scale-out.

It subclasses ``InMemorySaver`` to reuse LangGraph's exact in-memory
bookkeeping (versions, pending writes, channel history) and simply
write-throughs each thread's slice to Cosmos on every ``put``/``put_writes``,
hydrating a thread lazily the first time it is read or written.

Selection mirrors ``store.py``: Cosmos when ``COSMOS_ENDPOINT`` is set, else the
plain in-memory saver (local dev and tests are unaffected).
"""

from __future__ import annotations

import base64
import os
import pickle
from typing import Any, Optional

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

# Reuse the existing Cosmos container (partition key ``/sessionId``); no new
# infrastructure is required.
_CONTAINER = "sessions"


class CosmosCheckpointSaver(InMemorySaver):
    """An ``InMemorySaver`` that persists each thread's state to Cosmos DB."""

    def __init__(
        self,
        endpoint: str,
        *,
        serde: Any | None = None,
        container: Any | None = None,
    ) -> None:
        super().__init__(serde=serde)
        if container is not None:
            self._cx = container
        else:
            from azure.cosmos import CosmosClient
            from azure.identity import DefaultAzureCredential

            client_id = os.getenv("AZURE_CLIENT_ID")
            cred = (
                DefaultAzureCredential(managed_identity_client_id=client_id)
                if client_id
                else DefaultAzureCredential()
            )
            client = CosmosClient(endpoint, credential=cred)
            db = client.get_database_client("harness")
            self._cx = db.get_container_client(_CONTAINER)
        self._loaded: set[str] = set()

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _thread_id(config: dict[str, Any]) -> Optional[str]:
        return (config or {}).get("configurable", {}).get("thread_id")

    def _ensure_loaded(self, thread_id: Optional[str]) -> None:
        """Pull a thread's persisted slice from Cosmos into memory once."""
        if not thread_id or thread_id in self._loaded:
            return
        self._loaded.add(thread_id)
        try:
            doc = self._cx.read_item(item=thread_id, partition_key=thread_id)
        except Exception:
            return  # first time we've seen this thread
        try:
            storage, writes, blobs = pickle.loads(base64.b64decode(doc["data"]))
        except Exception:
            return  # unreadable/legacy blob - start fresh
        for ns, inner in storage.items():
            self.storage[thread_id][ns].update(inner)
        for key, inner in writes.items():
            self.writes[key].update(inner)
        self.blobs.update(blobs)

    def _persist(self, thread_id: Optional[str]) -> None:
        """Serialize this thread's slice of state and upsert it to Cosmos."""
        if not thread_id:
            return
        storage = {ns: dict(inner) for ns, inner in self.storage.get(thread_id, {}).items()}
        writes = {k: dict(v) for k, v in self.writes.items() if k[0] == thread_id}
        blobs = {k: v for k, v in self.blobs.items() if k[0] == thread_id}
        data = base64.b64encode(pickle.dumps((storage, writes, blobs))).decode()
        try:
            self._cx.upsert_item(
                {"id": thread_id, "sessionId": thread_id, "data": data}
            )
        except Exception:
            pass  # durability is best-effort; the run still works in memory

    # -- overrides (hydrate before use, persist after mutation) ------------
    def get_tuple(self, config):
        self._ensure_loaded(self._thread_id(config))
        return super().get_tuple(config)

    def list(self, config, **kwargs):
        if config:
            self._ensure_loaded(self._thread_id(config))
        return super().list(config, **kwargs)

    def put(self, config, checkpoint, metadata, new_versions):
        thread_id = self._thread_id(config)
        self._ensure_loaded(thread_id)
        result = super().put(config, checkpoint, metadata, new_versions)
        self._persist(thread_id)
        return result

    def put_writes(self, config, writes, task_id, task_path=""):
        thread_id = self._thread_id(config)
        self._ensure_loaded(thread_id)
        super().put_writes(config, writes, task_id, task_path)
        self._persist(thread_id)


def get_checkpointer() -> BaseCheckpointSaver:
    """Return a Cosmos-backed saver when configured, else an in-memory one."""
    endpoint = os.getenv("COSMOS_ENDPOINT")
    if endpoint:
        try:
            return CosmosCheckpointSaver(endpoint)
        except Exception:
            pass  # fall back to memory if Cosmos is unreachable
    return InMemorySaver()
