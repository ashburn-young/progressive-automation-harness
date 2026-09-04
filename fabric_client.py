"""fabric_client.py - real Microsoft Fabric operations via the Fabric REST API.

Lets the deployed harness act on a live Fabric workspace using its managed
identity (AAD, no keys). Enabled only when ``FABRIC_WORKSPACE_ID`` is set and the
identity has been granted a workspace role; otherwise every call reports it is
disabled and the caller falls back to the simulated tools. All calls are
defensive and return a human-readable string (``[fabric] ...`` / ``[fabric-error]
...``) so a failure never breaks a run.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Optional

_API = "https://api.fabric.microsoft.com/v1"
_SCOPE = "https://api.fabric.microsoft.com/.default"


def workspace_id() -> Optional[str]:
    return os.getenv("FABRIC_WORKSPACE_ID")


def is_enabled() -> bool:
    return bool(workspace_id())


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


def _call(path: str, method: str = "GET", body: Optional[dict] = None):
    token = _token()
    if not token:
        return None, "no token"
    headers = {"Authorization": "Bearer " + token}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    req = urllib.request.Request(_API + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return (json.loads(raw) if raw else {}), None
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode(errors="replace")[:200]
        except Exception:
            pass
        return None, f"HTTP {exc.code}: {detail}"
    except Exception as exc:
        return None, f"{exc.__class__.__name__}: {str(exc)[:150]}"


def create_lakehouse(name: str) -> str:
    ws = workspace_id()
    data, err = _call(f"/workspaces/{ws}/lakehouses", "POST", {"displayName": name})
    if err:
        return f"[fabric-error] create lakehouse '{name}': {err}"
    return f"[fabric] Created lakehouse '{name}' (id {data.get('id')})."


def create_pipeline(name: str, description: str = "") -> str:
    ws = workspace_id()
    data, err = _call(
        f"/workspaces/{ws}/dataPipelines",
        "POST",
        {"displayName": name, "description": description or "Created by the harness."},
    )
    if err:
        return f"[fabric-error] create pipeline '{name}': {err}"
    return f"[fabric] Created pipeline '{name}' (id {data.get('id')}) in the workspace."


def _pipeline_id_by_name(name: str) -> Optional[str]:
    ws = workspace_id()
    data, err = _call(f"/workspaces/{ws}/dataPipelines")
    if err or not data:
        return None
    for item in data.get("value", []):
        if item.get("displayName") == name:
            return item.get("id")
    return None


def create_dataflow(name: str, query: str = "") -> str:
    ws = workspace_id()
    data, err = _call(
        f"/workspaces/{ws}/dataflows",
        "POST",
        {"displayName": name, "description": "Created by the harness."},
    )
    if err:
        return f"[fabric-error] create dataflow '{name}': {err}"
    return f"[fabric] Created dataflow '{name}' (id {data.get('id')})."


def run_pipeline(name: str) -> str:
    ws = workspace_id()
    pid = _pipeline_id_by_name(name)
    if not pid:
        return f"[fabric-error] run pipeline '{name}': pipeline not found."
    _data, err = _call(
        f"/workspaces/{ws}/items/{pid}/jobs/instances?jobType=Pipeline", "POST"
    )
    if err:
        return f"[fabric-error] run pipeline '{name}': {err}"
    return f"[fabric] Triggered pipeline '{name}' (id {pid}); a job instance is running."


def list_pipelines() -> str:
    ws = workspace_id()
    data, err = _call(f"/workspaces/{ws}/dataPipelines")
    if err:
        return f"[fabric-error] list pipelines: {err}"
    names = [i.get("displayName") for i in (data or {}).get("value", [])]
    return f"[fabric] Pipelines in the workspace: {', '.join(names) or '(none)'}."
