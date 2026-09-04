"""deploy_embeddings.py - deploy a text-embedding model to the AI Services account.

Both the RAG knowledge index (#6) and embeddings-based tool binding (#9) need an
embeddings model. Only the chat model (gpt-5.6-sol) is deployed, so this adds
``text-embedding-3-small`` (additive and reversible). Auth = signed-in identity.

    python infra/deploy_embeddings.py
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
DEPLOYMENT = "text-embedding-3-small"
MODEL = "text-embedding-3-small"
API = "api-version=2024-10-01"
MGMT = "https://management.azure.com"


def _tok() -> str:
    return DefaultAzureCredential().get_token(MGMT + "/.default").token


def _req(url, method="GET", body=None, tok=None):
    headers = {"Authorization": "Bearer " + tok} if tok else {}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    return urllib.request.Request(url, data=data, method=method, headers=headers)


def main() -> None:
    tok = _tok()
    url = (
        f"{MGMT}/subscriptions/{SUB}/resourceGroups/{RG}"
        f"/providers/Microsoft.CognitiveServices/accounts/{ACCOUNT}"
        f"/deployments/{DEPLOYMENT}?{API}"
    )
    body = {
        "sku": {"name": "GlobalStandard", "capacity": 50},
        "properties": {
            "model": {"format": "OpenAI", "name": MODEL, "version": "1"}
        },
    }
    try:
        urllib.request.urlopen(_req(url, "PUT", body, tok))
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"PUT -> {exc.code}\n{exc.read().decode()}") from exc
    print(f"submitted deployment '{DEPLOYMENT}' ({MODEL})")

    for _ in range(40):
        time.sleep(10)
        cur = json.load(urllib.request.urlopen(_req(url, tok=_tok())))
        state = cur["properties"].get("provisioningState")
        print("provisioning:", state)
        if state in ("Succeeded", "Failed", "Canceled"):
            if state != "Succeeded":
                raise SystemExit(f"deployment ended: {state}")
            print("embeddings model ready")
            return


if __name__ == "__main__":
    main()
