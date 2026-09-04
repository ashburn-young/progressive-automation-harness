"""fabric_setup.py - attach a workspace to a Fabric capacity and grant the
harness identity access, so the deployed harness can act on real Fabric.

Uses the signed-in identity (Fabric admin) via the Fabric REST API.

    python infra/fabric_setup.py
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from azure.identity import DefaultAzureCredential

FABRIC = "https://api.fabric.microsoft.com/v1"
WORKSPACE_ID = "129cfc63-73d6-4dac-82af-a73eaaec211b"  # PVH FY27 AI-for-BI PoC
CAPACITY_NAME = "fabforall"
# The harness container's user-assigned managed identity (service principal).
UAMI_PRINCIPAL_ID = "fb325123-25a9-41d6-a218-a50a70823a93"  # pah-id-qzbtlziciba4a


def _tok() -> str:
    return DefaultAzureCredential().get_token(
        "https://api.fabric.microsoft.com/.default"
    ).token


def _req(url, method="GET", body=None, tok=None):
    headers = {"Authorization": "Bearer " + tok} if tok else {}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    return urllib.request.Request(url, data=data, method=method, headers=headers)


def _call(url, method="GET", body=None, tok=None):
    try:
        with urllib.request.urlopen(_req(url, method, body, tok)) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise SystemExit(f"{method} {url} -> {exc.code}\n{detail}") from exc


def _capacity_id(tok: str, name: str) -> str:
    data = _call(f"{FABRIC}/capacities", tok=tok)
    for cap in data.get("value", []):
        if cap.get("displayName", "").lower() == name.lower():
            return cap["id"]
    raise SystemExit(f"capacity '{name}' not found")


def main() -> None:
    tok = _tok()
    cap_id = _capacity_id(tok, CAPACITY_NAME)
    print(f"capacity '{CAPACITY_NAME}' id: {cap_id}")

    _call(
        f"{FABRIC}/workspaces/{WORKSPACE_ID}/assignToCapacity",
        "POST",
        {"capacityId": cap_id},
        tok,
    )
    ws = _call(f"{FABRIC}/workspaces/{WORKSPACE_ID}", tok=tok)
    print(f"workspace '{ws.get('displayName')}' capacityId now: {ws.get('capacityId')}")

    if UAMI_PRINCIPAL_ID:
        _call(
            f"{FABRIC}/workspaces/{WORKSPACE_ID}/roleAssignments",
            "POST",
            {
                "principal": {
                    "id": UAMI_PRINCIPAL_ID,
                    "type": "ServicePrincipal",
                },
                "role": "Contributor",
            },
            tok,
        )
        print(f"granted principal {UAMI_PRINCIPAL_ID} Admin on the workspace")


if __name__ == "__main__":
    main()
