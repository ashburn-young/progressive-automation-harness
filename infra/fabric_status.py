"""fabric_status.py - check the demo pipeline run + lakehouse tables."""
import json
import urllib.error
import urllib.request

from azure.identity import DefaultAzureCredential

B = "https://api.fabric.microsoft.com/v1"
WS = "129cfc63-73d6-4dac-82af-a73eaaec211b"
PID = "1165f4ae-2a21-4be4-87b0-16ed151262d7"
LH = "0480267a-abd9-47ec-a1d9-c65e5c128dec"

t = DefaultAzureCredential().get_token("https://api.fabric.microsoft.com/.default").token
H = {"Authorization": "Bearer " + t}


def get(path):
    try:
        with urllib.request.urlopen(urllib.request.Request(B + path, headers=H)) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200]


s, r = get(f"/workspaces/{WS}/items/{PID}/jobs/instances")
inst = (r.get("value") or [{}])[0] if isinstance(r, dict) else {}
print("run status:", inst.get("status"), "| fail:",
      (inst.get("failureReason") or {}).get("message"))

s, tb = get(f"/workspaces/{WS}/lakehouses/{LH}/tables")
if isinstance(tb, dict):
    tables = tb.get("data") or tb.get("value") or []
    print("lakehouse tables:", [x.get("name") for x in tables])
else:
    print("tables:", s, tb)
