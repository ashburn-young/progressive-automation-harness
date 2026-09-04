"""verify_deployed_fabric.py - call the deployed /api/fabric/selftest behind Easy
Auth, proving the container's managed identity can build on real Fabric."""
import json
import time
import urllib.error
import urllib.request

from azure.identity import DefaultAzureCredential

APP_ID = "6f099fa8-71bf-4478-b4a6-1d292af4c039"
URL = (
    "https://pah-orchestrator.braveforest-e1e276d5.swedencentral."
    "azurecontainerapps.io/api/fabric/selftest"
)

cred = DefaultAzureCredential()
token = None
for scope in (f"api://{APP_ID}/.default", f"{APP_ID}/.default"):
    try:
        token = cred.get_token(scope).token
        print("got app token via", scope)
        break
    except Exception as exc:
        print("token scope failed", scope, type(exc).__name__)

if not token:
    raise SystemExit("could not get an app-audience token; sign-in required.")

for i in range(12):
    req = urllib.request.Request(
        URL, data=b"{}", method="POST",
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print("RESULT", r.status, r.read().decode())
        break
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()[:200]
        print(f"attempt {i}: HTTP {exc.code} {body}")
        if exc.code in (401, 403):
            break  # auth issue, retrying won't help
        time.sleep(10)
    except Exception as exc:
        print(f"attempt {i}: {type(exc).__name__}")
        time.sleep(10)
