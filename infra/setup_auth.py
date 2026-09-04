"""setup_auth.py - lock the orchestrator behind Microsoft Entra ID sign-in.

Enables Azure Container Apps built-in authentication ("Easy Auth"):

  1. create (or reuse) an Entra ID app registration via Microsoft Graph,
  2. mint a client secret and store it as a Container App secret,
  3. write the app's ``authConfig`` so anonymous browsers are redirected to
     Microsoft sign-in and unauthenticated API calls get a 401.

Auth is the signed-in identity (VS Code / WAM broker). The client secret is
handled only in-process and stored directly into Azure; it is never printed.

    python infra/setup_auth.py
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from azure.identity import DefaultAzureCredential

SUB = "03b5ad16-7d76-4a8e-946d-e47a76d88bb7"
RG = "rg-takemyjob"
APP = "pah-orchestrator"
FQDN = "pah-orchestrator.braveforest-e1e276d5.swedencentral.azurecontainerapps.io"
APP_REG_NAME = "pah-orchestrator-auth"
SECRET_SETTING_NAME = "aad-client-secret"

MGMT = "https://management.azure.com"
GRAPH = "https://graph.microsoft.com/v1.0"
_cred = DefaultAzureCredential()


def _tok(scope: str) -> str:
    return _cred.get_token(scope).token


def _req(url, method="GET", body=None, tok=None):
    headers = {}
    if tok:
        headers["Authorization"] = "Bearer " + tok
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


def _tenant_id(mgmt_tok: str) -> str:
    data = _call(f"{MGMT}/tenants?api-version=2022-12-01", tok=mgmt_tok)
    return data["value"][0]["tenantId"]


_GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _guard_client_inputs(app_id: str, secret: str) -> None:
    """Fail fast on placeholder/malformed values (e.g. a literal '<appId>')."""
    if not _GUID_RE.match(app_id.strip()):
        raise SystemExit(
            "AAD_CLIENT_ID is not a valid GUID. Set it to your app "
            "registration's Application (client) ID (no angle brackets), e.g.\n"
            '  $env:AAD_CLIENT_ID="00000000-0000-0000-0000-000000000000"'
        )
    if "<" in secret or ">" in secret or not secret.strip():
        raise SystemExit(
            "AAD_CLIENT_SECRET looks like a placeholder. Set it to the actual "
            "client secret VALUE from the app registration."
        )


def _ensure_app_registration(graph_tok: str) -> tuple[str, str]:
    """Return (object_id, app_id) for the auth app registration, creating it."""
    redirect = f"https://{FQDN}/.auth/login/aad/callback"
    flt = urllib.parse.quote(f"displayName eq '{APP_REG_NAME}'")
    found = _call(f"{GRAPH}/applications?$filter={flt}", tok=graph_tok)
    if found.get("value"):
        app = found["value"][0]
        # Make sure the redirect URI + id-token issuance are present.
        _call(
            f"{GRAPH}/applications/{app['id']}",
            "PATCH",
            {
                "web": {
                    "redirectUris": [redirect],
                    "implicitGrantSettings": {"enableIdTokenIssuance": True},
                }
            },
            graph_tok,
        )
        return app["id"], app["appId"]

    created = _call(
        f"{GRAPH}/applications",
        "POST",
        {
            "displayName": APP_REG_NAME,
            "signInAudience": "AzureADMyOrg",
            "web": {
                "redirectUris": [redirect],
                "implicitGrantSettings": {"enableIdTokenIssuance": True},
            },
        },
        graph_tok,
    )
    return created["id"], created["appId"]


def _ensure_service_principal(graph_tok: str, app_id: str) -> None:
    flt = urllib.parse.quote(f"appId eq '{app_id}'")
    found = _call(f"{GRAPH}/servicePrincipals?$filter={flt}", tok=graph_tok)
    if found.get("value"):
        return
    _call(f"{GRAPH}/servicePrincipals", "POST", {"appId": app_id}, graph_tok)


def _new_client_secret(graph_tok: str, object_id: str) -> str:
    resp = _call(
        f"{GRAPH}/applications/{object_id}/addPassword",
        "POST",
        {"passwordCredential": {"displayName": "easy-auth"}},
        graph_tok,
    )
    return resp["secretText"]


def _store_secret_on_app(mgmt_tok: str, secret_value: str) -> None:
    """Add the client secret via a targeted PATCH, then confirm it landed.

    A full-resource PUT can drop secrets that GET returns without values, so we
    merge only ``configuration.secrets`` and poll until the name is present.
    """
    url = (
        f"{MGMT}/subscriptions/{SUB}/resourceGroups/{RG}"
        f"/providers/Microsoft.App/containerApps/{APP}?api-version=2024-03-01"
    )
    cur = _call(url, tok=mgmt_tok)
    existing = (cur["properties"].get("configuration", {}).get("secrets")) or []
    secrets = [
        {"name": s["name"]}
        for s in existing
        if s.get("name") and s.get("name") != SECRET_SETTING_NAME
    ]
    secrets.append({"name": SECRET_SETTING_NAME, "value": secret_value})
    _call(
        url,
        "PATCH",
        {"properties": {"configuration": {"secrets": secrets}}},
        mgmt_tok,
    )

    # Confirm the secret is registered before referencing it from authConfig.
    for _ in range(10):
        latest = _call(url, tok=mgmt_tok)
        names = {
            s.get("name")
            for s in (latest["properties"].get("configuration", {}).get("secrets") or [])
        }
        if SECRET_SETTING_NAME in names:
            return
        time.sleep(3)
    raise SystemExit(
        f"secret '{SECRET_SETTING_NAME}' did not register on the container app"
    )


def _write_auth_config(mgmt_tok: str, tenant_id: str, app_id: str) -> None:
    url = (
        f"{MGMT}/subscriptions/{SUB}/resourceGroups/{RG}"
        f"/providers/Microsoft.App/containerApps/{APP}"
        f"/authConfigs/current?api-version=2024-03-01"
    )
    body = {
        "properties": {
            "platform": {"enabled": True},
            "globalValidation": {
                "unauthenticatedClientAction": "RedirectToLoginPage",
                "redirectToProvider": "azureactivedirectory",
            },
            "identityProviders": {
                "azureActiveDirectory": {
                    "enabled": True,
                    "registration": {
                        "openIdIssuer": (
                            f"https://login.microsoftonline.com/{tenant_id}/v2.0"
                        ),
                        "clientId": app_id,
                        "clientSecretSettingName": SECRET_SETTING_NAME,
                    },
                    "validation": {
                        "allowedAudiences": [f"api://{app_id}", app_id]
                    },
                }
            },
        }
    }
    # The secret ref can lag registration; retry on SecretRefNotFound.
    last_err = None
    for _ in range(8):
        try:
            _call(url, "PUT", body, mgmt_tok)
            return
        except SystemExit as exc:
            last_err = str(exc)
            if "AuthConfigSecretRefNotFound" not in last_err:
                raise
            time.sleep(5)
    raise SystemExit(last_err or "authConfig write failed")


def main() -> None:
    graph_tok = _tok("https://graph.microsoft.com/.default")
    mgmt_tok = _tok(MGMT + "/.default")

    tenant_id = _tenant_id(mgmt_tok)
    print("tenant:", tenant_id)

    # Fallback: if the signed-in account can't create app registrations (needs
    # Application Administrator in Entra), supply an already-created app via env
    # so only the Azure-side wiring runs. The secret is read from the terminal
    # environment and never passes through any assistant/model.
    env_app_id = os.getenv("AAD_CLIENT_ID")
    env_secret = os.getenv("AAD_CLIENT_SECRET")
    if env_app_id and env_secret:
        print("using AAD_CLIENT_ID from environment (management-only mode)")
        _guard_client_inputs(env_app_id, env_secret)
        _store_secret_on_app(mgmt_tok, env_secret)
        print("client secret stored on container app (value not shown)")
        _write_auth_config(mgmt_tok, tenant_id, env_app_id)
        print("authConfig written -> Easy Auth (Entra ID) enabled")
        print(f"sign-in now required at https://{FQDN}")
        return

    object_id, app_id = _ensure_app_registration(graph_tok)
    print("app registration objectId:", object_id)
    print("app (client) id:", app_id)

    _ensure_service_principal(graph_tok, app_id)
    print("service principal ensured")

    secret_value = _new_client_secret(graph_tok, object_id)
    _store_secret_on_app(mgmt_tok, secret_value)
    print("client secret stored on container app (value not shown)")

    _write_auth_config(mgmt_tok, tenant_id, app_id)
    print("authConfig written -> Easy Auth (Entra ID) enabled")
    print(f"sign-in now required at https://{FQDN}")


if __name__ == "__main__":
    main()
