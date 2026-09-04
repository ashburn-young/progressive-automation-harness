"""deploy_models.py - deploy a tiered lineup of chat models for model selection.

All GlobalStandard (pay-per-token, ~$0 idle). Gives the UI a real choice plus an
"auto" tier. Auth = signed-in identity. Idempotent (PUT is create-or-update).

    python infra/deploy_models.py
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from azure.identity import DefaultAzureCredential

SUB = "03b5ad16-7d76-4a8e-946d-e47a76d88bb7"
RG = "rg-takemyjob"
ACCOUNT = "takemyjob"
API = "api-version=2024-10-01"
MGMT = "https://management.azure.com"

# name -> version. GlobalStandard, modest capacity (in thousands of TPM).
MODELS = {
    "gpt-5.6-luna": "2026-07-09",
    "gpt-5.4-mini": "2026-03-17",
    "gpt-5.4-nano": "2026-03-17",
}
CAPACITY = 50


def _tok() -> str:
    return DefaultAzureCredential().get_token(MGMT + "/.default").token


def _req(url, method="GET", body=None, tok=None):
    headers = {"Authorization": "Bearer " + tok} if tok else {}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    return urllib.request.Request(url, data=data, method=method, headers=headers)


def deploy(name: str, version: str) -> None:
    tok = _tok()
    url = (
        f"{MGMT}/subscriptions/{SUB}/resourceGroups/{RG}"
        f"/providers/Microsoft.CognitiveServices/accounts/{ACCOUNT}"
        f"/deployments/{name}?{API}"
    )
    body = {
        "sku": {"name": "GlobalStandard", "capacity": CAPACITY},
        "properties": {
            "model": {"format": "OpenAI", "name": name, "version": version}
        },
    }
    try:
        urllib.request.urlopen(_req(url, "PUT", body, tok))
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"PUT {name} -> {exc.code}\n{exc.read().decode()}") from exc
    print(f"submitted '{name}' ({version})")
    for _ in range(40):
        time.sleep(10)
        cur = json.load(urllib.request.urlopen(_req(url, tok=_tok())))
        state = cur["properties"].get("provisioningState")
        if state in ("Succeeded", "Failed", "Canceled"):
            print(f"  {name}: {state}")
            if state != "Succeeded":
                raise SystemExit(f"{name} ended: {state}")
            return


def main() -> None:
    for name, version in MODELS.items():
        deploy(name, version)
    print("all models ready")


if __name__ == "__main__":
    main()
