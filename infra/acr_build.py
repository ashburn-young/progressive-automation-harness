"""acr_build.py - build the orchestrator image in ACR and deploy it.

Uses ACR Tasks "quick build" (server-side Docker build, no local daemon):
  1. upload the build context to the registry,
  2. schedule a Docker build/push run,
  3. poll it, then
  4. point the pah-orchestrator Container App at the new image.

Auth is the signed-in identity (VS Code / WAM broker) via the management API.

    python infra/acr_build.py
"""

from __future__ import annotations

import io
import json
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from azure.identity import DefaultAzureCredential

SUB = "03b5ad16-7d76-4a8e-946d-e47a76d88bb7"
RG = "rg-takemyjob"
ACR = "pahacrqzbtlziciba4a"
LOGIN_SERVER = "pahacrqzbtlziciba4a.azurecr.io"
APP = "pah-orchestrator"
# Fresh tag per build so the Container App pulls a new revision.
IMAGE = "pah/orchestrator:" + time.strftime("v%Y%m%d%H%M%S")
MGMT = "https://management.azure.com"
ROOT = Path(__file__).parent.parent  # the harness folder = build context

# Paths (relative) to exclude from the uploaded build context.
_EXCLUDE_DIRS = {".venv", "__pycache__", ".git", "skills", "deployed", ".pytest_cache"}
_EXCLUDE_FILES = {
    ".env",
    "dspy_training_logs.jsonl",
    "infra/azuredeploy.json",
    "infra/azuredeploy.parameters.json",
}


def _tok() -> str:
    return DefaultAzureCredential().get_token(MGMT + "/.default").token


def _req(url, method="GET", body=None, tok=None, headers=None):
    h = dict(headers or {})
    if tok:
        h["Authorization"] = "Bearer " + tok
    data = None
    if body is not None and not isinstance(body, (bytes, bytearray)):
        h["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    elif isinstance(body, (bytes, bytearray)):
        data = body
    return urllib.request.Request(url, data=data, method=method, headers=h)


def _make_context_tar() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path in ROOT.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(ROOT).as_posix()
            if any(part in _EXCLUDE_DIRS for part in path.relative_to(ROOT).parts):
                continue
            if rel in _EXCLUDE_FILES or rel.endswith(".pyc"):
                continue
            tar.add(str(path), arcname=rel)
    return buf.getvalue()


def build_image(tok: str) -> None:
    rid = (
        f"{MGMT}/subscriptions/{SUB}/resourceGroups/{RG}"
        f"/providers/Microsoft.ContainerRegistry/registries/{ACR}"
    )
    api = "api-version=2019-06-01-preview"

    up = json.load(
        urllib.request.urlopen(
            _req(f"{rid}/listBuildSourceUploadUrl?{api}", "POST", {}, tok)
        )
    )
    relative_path, upload_url = up["relativePath"], up["uploadUrl"]

    tar = _make_context_tar()
    print(f"context: {len(tar) // 1024} KiB -> uploading")
    urllib.request.urlopen(
        _req(upload_url, "PUT", bytes(tar), headers={"x-ms-blob-type": "BlockBlob"})
    )

    run_req = {
        "type": "DockerBuildRequest",
        "sourceLocation": relative_path,
        "dockerFilePath": "Dockerfile",
        "imageNames": [IMAGE],
        "isPushEnabled": True,
        "platform": {"os": "Linux", "architecture": "amd64"},
        "agentConfiguration": {"cpu": 2},
    }
    run = json.load(
        urllib.request.urlopen(_req(f"{rid}/scheduleRun?{api}", "POST", run_req, tok))
    )
    run_id = run.get("name") or run.get("properties", {}).get("runId")
    print("build run:", run_id)

    for _ in range(80):
        time.sleep(15)
        try:
            r = json.load(
                urllib.request.urlopen(_req(f"{rid}/runs/{run_id}?{api}", tok=_tok()))
            )
        except Exception as exc:
            print("poll retry", type(exc).__name__)
            continue
        status = r["properties"].get("status")
        print("build:", status)
        if status in ("Succeeded", "Failed", "Canceled", "Error", "Timeout"):
            if status != "Succeeded":
                raise SystemExit(f"ACR build ended: {status}")
            return


def update_app(tok: str) -> None:
    url = (
        f"{MGMT}/subscriptions/{SUB}/resourceGroups/{RG}"
        f"/providers/Microsoft.App/containerApps/{APP}?api-version=2024-03-01"
    )
    cur = json.load(urllib.request.urlopen(_req(url, tok=tok)))
    props = cur["properties"]
    props["template"]["containers"][0]["image"] = f"{LOGIN_SERVER}/{IMAGE}"
    # PATCH (merge) so we only change the template; existing secrets (e.g. the
    # Easy Auth client secret, masked on GET) and auth config are preserved.
    body = {"properties": {"template": props["template"]}}
    urllib.request.urlopen(_req(url, "PATCH", body, tok))
    print("container app updated -> image:", f"{LOGIN_SERVER}/{IMAGE}")
    print("fqdn:", props.get("configuration", {}).get("ingress", {}).get("fqdn"))


def main() -> None:
    tok = _tok()
    build_image(tok)
    update_app(_tok())


if __name__ == "__main__":
    main()
