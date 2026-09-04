"""create_test_user.py - create a test member user that can sign into the app.

The password is randomly generated and NEVER printed or returned, so no
credential passes through the assistant. After creation, reset the password in
the Entra portal (Users -> the user -> Reset password) to get a usable one.
"""
import json
import secrets
import string
import urllib.error
import urllib.request

from azure.identity import DefaultAzureCredential

UPN = "experience@mngenvmcap999938.onmicrosoft.com"
DISPLAY = "Experience Test User"
NICK = "experience"

tok = DefaultAzureCredential().get_token("https://graph.microsoft.com/.default").token
pw = "".join(
    secrets.choice(string.ascii_letters + string.digits + "!@#$%^&*") for _ in range(20)
)
body = {
    "accountEnabled": True,
    "displayName": DISPLAY,
    "mailNickname": NICK,
    "userPrincipalName": UPN,
    "passwordProfile": {"forceChangePasswordNextSignIn": True, "password": pw},
}
req = urllib.request.Request(
    "https://graph.microsoft.com/v1.0/users",
    data=json.dumps(body).encode(),
    method="POST",
    headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"},
)
try:
    with urllib.request.urlopen(req) as r:
        d = json.load(r)
    print("CREATED user:", d.get("userPrincipalName"), "| id", d.get("id"))
    print("Next: reset its password in the Entra portal to sign in.")
except urllib.error.HTTPError as e:
    print("ERROR", e.code, e.read().decode()[:400])
