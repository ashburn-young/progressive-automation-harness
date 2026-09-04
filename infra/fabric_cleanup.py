"""fabric_cleanup.py - delete the demo artifacts created in the PVH workspace."""
import json
import urllib.error
import urllib.request

from azure.identity import DefaultAzureCredential

B = "https://api.fabric.microsoft.com/v1"
WS = "129cfc63-73d6-4dac-82af-a73eaaec211b"
PIPELINES = {"pah-demo-pipeline", "harness-ingest-iris", "pah-container-selftest"}
LAKEHOUSES = {"harness_lakehouse"}

t = DefaultAzureCredential().get_token("https://api.fabric.microsoft.com/.default").token
H = {"Authorization": "Bearer " + t}


def call(path, method="GET"):
    try:
        with urllib.request.urlopen(
            urllib.request.Request(B + path, method=method, headers=H)
        ) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:150]


def purge(kind, names):
    _s, data = call(f"/workspaces/{WS}/{kind}")
    for item in (data or {}).get("value", []) if isinstance(data, dict) else []:
        if item.get("displayName") in names:
            s, _ = call(f"/workspaces/{WS}/{kind}/{item['id']}", "DELETE")
            print(f"deleted {kind} '{item['displayName']}' -> {s}")


purge("dataPipelines", PIPELINES)
purge("lakehouses", LAKEHOUSES)
print("cleanup done")
