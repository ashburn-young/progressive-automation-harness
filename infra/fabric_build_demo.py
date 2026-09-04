"""fabric_build_demo.py - build a richer end-to-end demo in the PVH workspace.

Creates a Lakehouse (the table destination) and a Data pipeline that contains a
real Copy activity (public CSV -> Lakehouse table), via the Fabric REST API using
the signed-in identity. Prints the API responses so results are transparent.

    python infra/fabric_build_demo.py
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request

from azure.identity import DefaultAzureCredential

FABRIC = "https://api.fabric.microsoft.com/v1"
WS = "129cfc63-73d6-4dac-82af-a73eaaec211b"  # PVH FY27 AI-for-BI PoC
LAKEHOUSE = "harness_lakehouse"
PIPELINE = "harness-ingest-iris"
SOURCE_URL = "https://raw.githubusercontent.com/plotly/datasets/master/iris.csv"


def _tok() -> str:
    return DefaultAzureCredential().get_token(
        "https://api.fabric.microsoft.com/.default"
    ).token


def _call(path, method="GET", body=None, tok=None):
    headers = {"Authorization": "Bearer " + tok}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    req = urllib.request.Request(FABRIC + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace")[:400]


def _lakehouse_id(tok: str) -> str:
    _s, data = _call(f"/workspaces/{WS}/lakehouses", tok=tok)
    for lh in (data or {}).get("value", []):
        if lh.get("displayName") == LAKEHOUSE:
            return lh["id"]
    status, data = _call(
        f"/workspaces/{WS}/lakehouses", "POST", {"displayName": LAKEHOUSE}, tok
    )
    print("create lakehouse:", status, json.dumps(data)[:200])
    if isinstance(data, dict):
        return data.get("id", "")
    return ""


def _pipeline_content(lakehouse_id: str, connection_id: str) -> dict:
    """A Copy activity: anonymous HTTP CSV -> Lakehouse table 'iris'."""
    return {
        "properties": {
            "activities": [
                {
                    "name": "Copy iris CSV",
                    "type": "Copy",
                    "typeProperties": {
                        "source": {
                            "type": "DelimitedTextSource",
                            "storeSettings": {
                                "type": "HttpReadSettings",
                                "requestMethod": "GET",
                            },
                            "formatSettings": {
                                "type": "DelimitedTextReadSettings"
                            },
                            "datasetSettings": {
                                "type": "DelimitedText",
                                "typeProperties": {
                                    "location": {
                                        "type": "HttpServerLocation",
                                        "relativeUrl": "",
                                    },
                                    "columnDelimiter": ",",
                                    "firstRowAsHeader": True,
                                },
                                "externalReferences": {
                                    "connection": connection_id
                                },
                            },
                        },
                        "sink": {
                            "type": "LakehouseTableSink",
                            "datasetSettings": {
                                "type": "LakehouseTable",
                                "typeProperties": {"table": "iris"},
                                "linkedService": {
                                    "name": LAKEHOUSE,
                                    "properties": {
                                        "type": "Lakehouse",
                                        "typeProperties": {
                                            "artifactId": lakehouse_id,
                                            "workspaceId": WS,
                                        },
                                    },
                                },
                            },
                        },
                    },
                }
            ]
        }
    }


def _http_connection(tok: str) -> str:
    """Create (or return) an anonymous HTTP connection whose test GET succeeds."""
    name = "harness-public-http"
    _s, data = _call("/connections", tok=tok)
    for c in (data or {}).get("value", []) if isinstance(data, dict) else []:
        if c.get("displayName") == name:
            return c["id"]
    body = {
        "connectivityType": "ShareableCloud",
        "displayName": name,
        "connectionDetails": {
            "type": "HttpServer",
            "creationMethod": "HttpServer",
            # Point at the full CSV so the mandatory test GET returns 200.
            "parameters": [
                {"dataType": "Text", "name": "url", "value": SOURCE_URL}
            ],
        },
        "credentialDetails": {
            "singleSignOnType": "None",
            "connectionEncryption": "NotEncrypted",
            "credentials": {"credentialType": "Anonymous"},
        },
    }
    status, data = _call("/connections", "POST", body, tok)
    print("create connection:", status, json.dumps(data)[:300] if isinstance(data, dict) else data)
    return data.get("id", "") if isinstance(data, dict) else ""


def _pipeline_id_by_name(tok: str) -> str:
    _s, data = _call(f"/workspaces/{WS}/dataPipelines", tok=tok)
    for p in (data or {}).get("value", []) if isinstance(data, dict) else []:
        if p.get("displayName") == PIPELINE:
            return p["id"]
    return ""


def main() -> None:
    tok = _tok()
    lh = _lakehouse_id(tok)
    print("lakehouse id:", lh)

    conn = _http_connection(tok)
    print("connection id:", conn)
    if not conn:
        print("no connection -> cannot build a runnable copy; stopping.")
        return

    content = _pipeline_content(lh, conn)
    payload = base64.b64encode(json.dumps(content).encode()).decode()
    part = {
        "path": "pipeline-content.json",
        "payload": payload,
        "payloadType": "InlineBase64",
    }

    pid = _pipeline_id_by_name(tok)
    if pid:
        status, data = _call(
            f"/workspaces/{WS}/dataPipelines/{pid}/updateDefinition",
            "POST",
            {"definition": {"parts": [part]}},
            tok,
        )
        print("update pipeline definition:", status, str(data)[:200])
    else:
        status, data = _call(
            f"/workspaces/{WS}/dataPipelines",
            "POST",
            {"displayName": PIPELINE, "definition": {"parts": [part]}},
            tok,
        )
        pid = data.get("id") if isinstance(data, dict) else ""
        print("create pipeline:", status, str(data)[:200])

    if pid:
        status, data = _call(
            f"/workspaces/{WS}/items/{pid}/jobs/instances?jobType=Pipeline",
            "POST",
            tok=tok,
        )
        print("run pipeline:", status, str(data)[:200])


if __name__ == "__main__":
    main()
