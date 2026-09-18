"""Configure a throwaway Keycloak realm for verifying OAuth on /mcp.

Run against a local dev-mode Keycloak:

    docker run -d --name asy-keycloak -p 8080:8080 \
      -e KC_BOOTSTRAP_ADMIN_USERNAME=admin -e KC_BOOTSTRAP_ADMIN_PASSWORD=admin \
      quay.io/keycloak/keycloak:26.3 start-dev
    python scripts/oauth_smoke/keycloak_realm.py

It creates realm `clio` with:

  * user `analyst` / `analyst` (analyst@agency.test);
  * a default client scope that puts `clio` in every token's `aud`,
    so OIDC_AUDIENCE=clio verifies;
  * an optional client scope `clio-mcp` for trying MCP_OAUTH_SCOPE, carrying
    the same audience mapper plus the email claim (a dynamically registered
    client that asks only for this scope gets only this scope);
  * anonymous dynamic client registration allowed (the realm's default
    anonymous policies block unknown hosts) - what Claude Desktop, claude.ai
    and `claude mcp login` do by default;
  * a pre-registered public client `claude-code` with redirect
    http://localhost:8123/callback, for the `--client-id` path.

Then start the app with:

    IDENTITY_PROVIDER=oidc OIDC_ISSUER=http://localhost:8080/realms/clio
    OIDC_AUDIENCE=clio MCP_PUBLIC_URL=http://localhost:8473/mcp

and see README.md in this directory for the client runs. Local throwaway
only: plaintext http and a known admin password are fine for a laptop and
for nothing else.
"""
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

KC = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080"
REALM = "clio"


def call(method, path, body=None, token=None, form=None):
    data = None
    headers = {}
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(KC + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def main():
    status, tok = call("POST", "/realms/master/protocol/openid-connect/token",
                       form={"grant_type": "password", "client_id": "admin-cli",
                             "username": "admin", "password": "admin"})
    assert status == 200, (status, tok)
    admin = tok["access_token"]

    status, _ = call("GET", f"/admin/realms/{REALM}", token=admin)
    if status == 404:
        status, out = call("POST", "/admin/realms", body={
            "realm": REALM, "enabled": True, "displayName": "Clio test realm",
        }, token=admin)
        assert status == 201, (status, out)
        print("realm created")
    else:
        print("realm exists")

    status, users = call("GET", f"/admin/realms/{REALM}/users?username=analyst", token=admin)
    if not users:
        status, out = call("POST", f"/admin/realms/{REALM}/users", body={
            "username": "analyst", "email": "analyst@agency.test", "emailVerified": True,
            "enabled": True, "firstName": "Ana", "lastName": "Lyst",
            "credentials": [{"type": "password", "value": "analyst", "temporary": False}],
        }, token=admin)
        assert status == 201, (status, out)
        print("user created")

    status, scopes = call("GET", f"/admin/realms/{REALM}/client-scopes", token=admin)
    by_name = {s["name"]: s for s in scopes}

    def ensure_scope(name, mappers, default=True):
        if name not in by_name:
            status, out = call("POST", f"/admin/realms/{REALM}/client-scopes", body={
                "name": name, "protocol": "openid-connect",
                "attributes": {"include.in.token.scope": "true", "display.on.consent.screen": "true"},
                "protocolMappers": mappers,
            }, token=admin)
            assert status == 201, (status, out)
            _, refreshed = call("GET", f"/admin/realms/{REALM}/client-scopes", token=admin)
            by_name.update({s["name"]: s for s in refreshed})
            print(f"client scope {name} created")
        sid = by_name[name]["id"]
        present = {m["name"] for m in by_name[name].get("protocolMappers", [])}
        for mapper in mappers:
            if mapper["name"] not in present:
                status, out = call("POST", f"/admin/realms/{REALM}/client-scopes/{sid}/protocol-mappers/models",
                                   body=mapper, token=admin)
                assert status == 201, (status, out)
                print(f"client scope {name}: mapper {mapper['name']} added")
        kind = "default-default-client-scopes" if default else "default-optional-client-scopes"
        status, out = call("PUT", f"/admin/realms/{REALM}/{kind}/{sid}", token=admin)
        assert status in (204, 409), (status, out)
        return sid

    audience_mapper = {
        "name": "clio-audience", "protocol": "openid-connect",
        "protocolMapper": "oidc-audience-mapper",
        "config": {"included.custom.audience": "clio", "access.token.claim": "true",
                   "id.token.claim": "false"},
    }
    email_mapper = {
        "name": "email", "protocol": "openid-connect",
        "protocolMapper": "oidc-usermodel-attribute-mapper",
        "config": {"user.attribute": "email", "claim.name": "email", "jsonType.label": "String",
                   "access.token.claim": "true", "id.token.claim": "true", "userinfo.token.claim": "true"},
    }
    ensure_scope("clio", [audience_mapper])
    # A client that registers itself with only the challenged scope (which is
    # what Claude Code does when MCP_OAUTH_SCOPE is set) gets ONLY this scope,
    # so the scope itself must carry the audience and the identity claim or
    # the token is (correctly) refused: no aud, no email.
    ensure_scope("clio-mcp", [audience_mapper, email_mapper], default=False)

    policy_type = "org.keycloak.services.clientregistration.policy.ClientRegistrationPolicy"
    status, comps = call("GET", f"/admin/realms/{REALM}/components?type={policy_type}", token=admin)
    for c in comps:
        if c.get("subType") == "anonymous" and c["providerId"] in (
            "trusted-hosts", "consent-required", "scope", "allowed-client-templates",
        ):
            status, out = call("DELETE", f"/admin/realms/{REALM}/components/{c['id']}", token=admin)
            assert status == 204, (status, out)
            print(f"dropped anonymous registration policy {c['providerId']}")

    status, clients = call("GET", f"/admin/realms/{REALM}/clients?clientId=claude-code", token=admin)
    if not clients:
        status, out = call("POST", f"/admin/realms/{REALM}/clients", body={
            "clientId": "claude-code", "name": "Claude Code (pre-registered)",
            "publicClient": True, "standardFlowEnabled": True, "directAccessGrantsEnabled": False,
            "redirectUris": ["http://localhost:8123/callback", "http://127.0.0.1:8123/callback"],
            "attributes": {"pkce.code.challenge.method": "S256"},
            "defaultClientScopes": ["profile", "email", "clio"],
            "optionalClientScopes": ["clio-mcp"],
        }, token=admin)
        assert status == 201, (status, out)
        print("pre-registered client claude-code created")

    status, disc = call("GET", f"/realms/{REALM}/.well-known/openid-configuration")
    print("issuer:", disc["issuer"])
    print("registration_endpoint:", disc.get("registration_endpoint"))
    print("jwks_uri:", disc["jwks_uri"])


if __name__ == "__main__":
    main()
