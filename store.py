"""store.py - persistence for skills and training traces.

Two backends, selected by the ``COSMOS_ENDPOINT`` environment variable:

* ``FileStore``   (default, local dev) - the existing ``skills/`` directory and
  ``dspy_training_logs.jsonl`` file. Behaviour is unchanged when Cosmos is not
  configured, so local runs and tests are unaffected.
* ``CosmosStore`` (in Azure) - the ``harness`` Cosmos database, using AAD via
  the managed identity. Skills live in the ``skills`` container, approved traces
  in ``trainingLogs``.

This is the "Memory & Context" layer of the harness architecture.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any, Optional

from skill import Skill, slugify
from skill_compiler import compile_from_records

BASE_DIR = Path(__file__).parent
SKILLS_DIR = BASE_DIR / "skills"
TRAINING_LOG = BASE_DIR / "dspy_training_logs.jsonl"


class FileStore:
    """Local filesystem persistence (default)."""

    kind = "file"

    def load_skill(self, task_name: str) -> Optional[Skill]:
        return Skill.load_for_task(task_name, SKILLS_DIR)

    def save_skill(self, skill: Skill) -> None:
        skill.save(SKILLS_DIR)

    def list_skills(self) -> list[Skill]:
        if not SKILLS_DIR.is_dir():
            return []
        out: list[Skill] = []
        for path in sorted(SKILLS_DIR.glob("*.json")):
            try:
                out.append(Skill.load(path))
            except Exception:
                continue
        return out

    def append_traces(self, traces: list[dict[str, Any]]) -> None:
        if not traces:
            return
        with TRAINING_LOG.open("a", encoding="utf-8") as fh:
            for trace in traces:
                fh.write(json.dumps(trace, ensure_ascii=False) + "\n")

    def read_traces(self, task_name: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if not TRAINING_LOG.exists():
            return out
        for line in TRAINING_LOG.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("task_name") == task_name:
                out.append(rec)
        return out


class CosmosStore:
    """Azure Cosmos DB persistence via AAD (managed identity)."""

    kind = "cosmos"

    def __init__(self, endpoint: str) -> None:
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
        self._skills = db.get_container_client("skills")
        self._logs = db.get_container_client("trainingLogs")

    def load_skill(self, task_name: str) -> Optional[Skill]:
        slug = slugify(task_name)
        try:
            item = self._skills.read_item(item=slug, partition_key=slug)
        except Exception:
            return None
        return Skill.model_validate(item["skill"])

    def save_skill(self, skill: Skill) -> None:
        self._skills.upsert_item(
            {
                "id": skill.slug,
                "slug": skill.slug,
                "taskName": skill.task_name,
                "skill": json.loads(skill.to_json()),
            }
        )

    def list_skills(self) -> list[Skill]:
        out: list[Skill] = []
        # The skills container is partitioned by /slug, so listing every skill is
        # a cross-partition query and must opt in explicitly.
        try:
            items = self._skills.query_items(
                query="SELECT * FROM c", enable_cross_partition_query=True
            )
        except TypeError:
            items = self._skills.query_items(query="SELECT * FROM c")
        try:
            for item in items:
                try:
                    out.append(Skill.model_validate(item["skill"]))
                except Exception:
                    continue
        except Exception:
            return out
        return out

    def append_traces(self, traces: list[dict[str, Any]]) -> None:
        for trace in traces:
            rec = dict(trace)
            rec["id"] = uuid.uuid4().hex
            rec["taskName"] = trace.get("task_name")
            self._logs.create_item(rec)

    def read_traces(self, task_name: str) -> list[dict[str, Any]]:
        return list(
            self._logs.query_items(
                query="SELECT * FROM c WHERE c.taskName = @t",
                parameters=[{"name": "@t", "value": task_name}],
                partition_key=task_name,
            )
        )


def get_store():
    """Return a CosmosStore when COSMOS_ENDPOINT is set, else a FileStore."""
    endpoint = os.getenv("COSMOS_ENDPOINT")
    if endpoint:
        try:
            return CosmosStore(endpoint)
        except Exception:
            pass  # fall back to files if Cosmos is unreachable
    return FileStore()


def compile_and_save(store, task_name: str, summary: str, required_inputs: list[str]) -> Skill:
    """Recompile a skill from the store's traces and persist it, preserving the
    lifecycle metadata (status, versions, publish state) of any existing skill."""
    records = store.read_traces(task_name)
    skill = compile_from_records(
        task_name, records, summary=summary, required_inputs=required_inputs
    )
    prev = store.load_skill(task_name)
    if prev is not None:
        skill.version = prev.version
        skill.status = prev.status
        skill.owner = prev.owner
        skill.published = prev.published
        skill.published_at = prev.published_at
        skill.last_validated_at = prev.last_validated_at
        skill.versions = prev.versions
        skill.created_at = prev.created_at
    store.save_skill(skill)
    return skill
