"""deploy.py - deploy the harness environment to rg-takemyjob.

Submits the compiled ARM template (azuredeploy.json + azuredeploy.parameters.json)
to Azure Resource Manager using the signed-in identity (VS Code / WAM broker).
No az CLI required.

    python infra/deploy.py
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from azure.identity import DefaultAzureCredential

SUB = "03b5ad16-7d76-4a8e-946d-e47a76d88bb7"
RG = "rg-takemyjob"
API = "2021-04-01"
HERE = Path(__file__).parent


def _req(url: str, tok: str, method: str = "GET", body: dict | None = None):
    headers = {"Authorization": "Bearer " + tok}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    return urllib.request.Request(url, data=data, method=method, headers=headers)


def main() -> None:
    template = json.loads((HERE / "azuredeploy.json").read_text(encoding="utf-8"))
    params_file = json.loads(
        (HERE / "azuredeploy.parameters.json").read_text(encoding="utf-8")
    )
    params = params_file.get("parameters", {})

    tok = DefaultAzureCredential().get_token(
        "https://management.azure.com/.default"
    ).token
    name = "pah-" + time.strftime("%Y%m%d%H%M%S")
    base = (
        f"https://management.azure.com/subscriptions/{SUB}/resourcegroups/{RG}"
        f"/providers/Microsoft.Resources/deployments/{name}"
    )
    url = f"{base}?api-version={API}"
    body = {"properties": {"mode": "Incremental", "template": template, "parameters": params}}

    try:
        r = urllib.request.urlopen(_req(url, tok, "PUT", body))
        print("submitted", r.status, "| deployment:", name)
    except urllib.error.HTTPError as e:
        print("SUBMIT ERROR", e.code)
        print(e.read().decode()[:2000])
        sys.exit(1)

    deadline = time.time() + 600
    while time.time() < deadline:
        time.sleep(15)
        d = json.load(urllib.request.urlopen(_req(url, tok)))
        state = d["properties"]["provisioningState"]
        print("state:", state)
        if state in ("Succeeded", "Failed", "Canceled"):
            if state == "Succeeded":
                outs = d["properties"].get("outputs", {})
                print("OUTPUTS:", json.dumps(outs, indent=2))
            else:
                ourl = f"{base}/operations?api-version={API}"
                od = json.load(urllib.request.urlopen(_req(ourl, tok)))
                for op in od.get("value", []):
                    p = op["properties"]
                    if p.get("provisioningState") == "Failed":
                        tr = p.get("targetResource", {})
                        print(
                            "FAILED:",
                            tr.get("resourceType"),
                            tr.get("resourceName"),
                            json.dumps(p.get("statusMessage"))[:500],
                        )
            return
    print("still running after 10min; check the portal for deployment", name)


if __name__ == "__main__":
    main()
