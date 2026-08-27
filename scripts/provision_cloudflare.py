"""Provision Cloudflare Tunnel + Access for Asymptote (docs/REMOTE-ACCESS.md).

One idempotent run creates everything deployment shape B needs:

  1. a remotely-managed tunnel named `asymptote`
  2. tunnel ingress:  <hostname> -> http://asymptote:8473
  3. a proxied CNAME  <hostname> -> <tunnel-id>.cfargotunnel.com
  4. Access app on the root hostname   (Allow: your email — browser SSO)
  5. Access app on <hostname>/mcp      (Service Auth: the token below)
  6. an Access service token for headless MCP clients

Secrets (TUNNEL_TOKEN, AUTH_PASSWORD, CF_ACCESS_CLIENT_ID/SECRET) are written
into the repo-local .env — which is gitignored — and never printed. The JWT
trust settings (CF_ACCESS_TEAM_DOMAIN, CF_ACCESS_AUD — the Access apps' AUD
tags) and MCP_ALLOWED_HOSTS are written too, so SSO logins skip the password
prompt and PRIVATE_COLLECTIONS=true can be enabled without further lookup.

Usage:
    CLOUDFLARE_API_TOKEN=... python scripts/provision_cloudflare.py
    # or put CLOUDFLARE_API_TOKEN=... in .env first, then run with no env var

The API token needs: Account/Cloudflare Tunnel/Edit,
Account/Access: Apps and Policies/Edit, Zone/DNS/Edit (zone: the apex below).
Safe to re-run: existing objects are found and reused, not duplicated.
"""

import json
import os
import re
import secrets
import sys
import urllib.error
import urllib.request

ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID", "71a9952ea49acb57979b8707f6666463")
HOSTNAME = os.environ.get("ASYMPTOTE_HOSTNAME", "asymptote.cyberlion.dev")
ALLOW_EMAIL = os.environ.get("ASYMPTOTE_ALLOW_EMAIL", "jordan.boyce@cyberlion.dev")
TUNNEL_NAME = "asymptote"
SERVICE_TOKEN_NAME = "asymptote-mcp"
API = "https://api.cloudflare.com/client/v4"
ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")

APEX = ".".join(HOSTNAME.split(".")[-2:])


def read_env():
    if not os.path.exists(ENV_PATH):
        return {}
    vals = {}
    for line in open(ENV_PATH, encoding="utf-8"):
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line.strip())
        if m:
            vals[m.group(1)] = m.group(2)
    return vals


def set_env(updates):
    """Update or append KEY=VALUE lines in .env without disturbing the rest."""
    lines = open(ENV_PATH, encoding="utf-8").read().splitlines() if os.path.exists(ENV_PATH) else []
    seen = set()
    for i, line in enumerate(lines):
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=", line.strip())
        if m and m.group(1) in updates:
            lines[i] = f"{m.group(1)}={updates[m.group(1)]}"
            seen.add(m.group(1))
    missing = [k for k in updates if k not in seen]
    if missing:
        lines += ["", "# ── Remote access (written by scripts/provision_cloudflare.py) ──"]
        lines += [f"{k}={updates[k]}" for k in missing]
    with open(ENV_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


TOKEN = os.environ.get("CLOUDFLARE_API_TOKEN") or read_env().get("CLOUDFLARE_API_TOKEN", "")
if not TOKEN:
    sys.exit("CLOUDFLARE_API_TOKEN not found in environment or .env — create one per the header comment.")


def cf(method, path, body=None, ok_codes=()):
    req = urllib.request.Request(
        API + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        data = json.load(e)
        codes = [err.get("code") for err in data.get("errors", [])]
        if not any(c in ok_codes for c in codes):
            sys.exit(f"{method} {path} failed: {data.get('errors')}")
    if not data.get("success", False):
        sys.exit(f"{method} {path} failed: {data.get('errors')}")
    return data["result"]


def main():
    who = cf("GET", "/user/tokens/verify")
    print(f"token ok (status: {who['status']})")

    zones = cf("GET", f"/zones?name={APEX}")
    if not zones:
        sys.exit(f"zone {APEX} not found on this account — is the domain on Cloudflare?")
    zone_id = zones[0]["id"]
    print(f"zone {APEX}: {zone_id}")

    # 1. Tunnel (remotely managed) ------------------------------------------
    tunnels = cf("GET", f"/accounts/{ACCOUNT_ID}/cfd_tunnel?name={TUNNEL_NAME}&is_deleted=false")
    tunnel = tunnels[0] if tunnels else cf(
        "POST", f"/accounts/{ACCOUNT_ID}/cfd_tunnel",
        {"name": TUNNEL_NAME, "config_src": "cloudflare"},
    )
    tid = tunnel["id"]
    print(f"tunnel '{TUNNEL_NAME}': {tid}")
    tunnel_token = cf("GET", f"/accounts/{ACCOUNT_ID}/cfd_tunnel/{tid}/token")

    # 2. Ingress: hostname -> the compose service ---------------------------
    cf("PUT", f"/accounts/{ACCOUNT_ID}/cfd_tunnel/{tid}/configurations", {
        "config": {"ingress": [
            {"hostname": HOSTNAME, "service": "http://asymptote:8473"},
            {"service": "http_status:404"},
        ]}
    })
    print(f"ingress: {HOSTNAME} -> http://asymptote:8473")

    # 3. DNS ----------------------------------------------------------------
    target = f"{tid}.cfargotunnel.com"
    recs = cf("GET", f"/zones/{zone_id}/dns_records?name={HOSTNAME}")
    body = {"type": "CNAME", "name": HOSTNAME, "content": target, "proxied": True,
            "comment": "Asymptote tunnel (scripts/provision_cloudflare.py)"}
    if recs:
        cf("PUT", f"/zones/{zone_id}/dns_records/{recs[0]['id']}", body)
    else:
        cf("POST", f"/zones/{zone_id}/dns_records", body)
    print(f"dns: {HOSTNAME} CNAME {target} (proxied)")

    # 4. Service token for MCP clients --------------------------------------
    env = read_env()
    existing = [t for t in cf("GET", f"/accounts/{ACCOUNT_ID}/access/service_tokens") or []
                if t["name"] == SERVICE_TOKEN_NAME]
    if existing and env.get("CF_ACCESS_CLIENT_ID"):
        st_id, client_id, client_secret = existing[0]["id"], env["CF_ACCESS_CLIENT_ID"], env["CF_ACCESS_CLIENT_SECRET"]
        print(f"service token '{SERVICE_TOKEN_NAME}': reusing (secret already in .env)")
    else:
        # The secret is only revealed at creation; if a token exists but we
        # don't hold its secret, mint a fresh one rather than orphaning MCP.
        name = SERVICE_TOKEN_NAME if not existing else f"{SERVICE_TOKEN_NAME}-{secrets.token_hex(3)}"
        st = cf("POST", f"/accounts/{ACCOUNT_ID}/access/service_tokens", {"name": name, "duration": "8760h"})
        st_id, client_id, client_secret = st["id"], st["client_id"], st["client_secret"]
        print(f"service token '{name}': created")

    # 5. Access apps --------------------------------------------------------
    apps = {a.get("domain"): a for a in cf("GET", f"/accounts/{ACCOUNT_ID}/access/apps") or []}

    def ensure_app(domain, name, policy):
        app = apps.get(domain)
        if app is None:
            app = cf("POST", f"/accounts/{ACCOUNT_ID}/access/apps", {
                "name": name, "domain": domain, "type": "self_hosted",
                "session_duration": "24h", "http_only_cookie_attribute": True,
            })
            cf("POST", f"/accounts/{ACCOUNT_ID}/access/apps/{app['id']}/policies", policy)
            print(f"access app '{name}' on {domain}: created")
        else:
            print(f"access app on {domain}: exists, leaving as-is")
        return app

    ui_app = ensure_app(HOSTNAME, "Asymptote", {
        "name": "owner", "decision": "allow", "precedence": 1,
        "include": [{"email": {"email": ALLOW_EMAIL}}],
    })
    mcp_app = ensure_app(f"{HOSTNAME}/mcp", "Asymptote MCP", {
        "name": "mcp-service-tokens", "decision": "non_identity", "precedence": 1,
        "include": [{"service_token": {"token_id": st_id}}],
    })

    # 6. JWT trust: team domain + both apps' AUD tags -----------------------
    # With these set the app verifies the Cf-Access-Jwt-Assertion the edge
    # attaches: SSO logins skip the Basic-auth prompt, MCP service tokens
    # carry their name as identity, and PRIVATE_COLLECTIONS=true becomes
    # possible (it refuses to start without them).
    org = cf("GET", f"/accounts/{ACCOUNT_ID}/access/organizations")
    auth_domain = org.get("auth_domain", "")
    auds = ",".join(a for a in {ui_app.get("aud", ""), mcp_app.get("aud", "")} if a)
    print(f"access jwt trust: team={auth_domain} auds={len(auds.split(','))} app(s)")

    # 7. Secrets + trust settings -> .env (gitignored) ----------------------
    updates = {
        "TUNNEL_TOKEN": tunnel_token,
        "CF_ACCESS_CLIENT_ID": client_id,
        "CF_ACCESS_CLIENT_SECRET": client_secret,
        "CF_ACCESS_TEAM_DOMAIN": auth_domain,
        "CF_ACCESS_AUD": auds,
        "MCP_ALLOWED_HOSTS": HOSTNAME,
    }
    if not env.get("AUTH_PASSWORD"):
        updates["AUTH_PASSWORD"] = secrets.token_urlsafe(33)
    set_env(updates)
    print(f"\nsecrets written to {ENV_PATH} (TUNNEL_TOKEN, CF_ACCESS_CLIENT_ID/SECRET, "
          "CF_ACCESS_TEAM_DOMAIN/AUD, MCP_ALLOWED_HOSTS"
          + (", AUTH_PASSWORD generated)" if "AUTH_PASSWORD" in updates else ")"))
    print("\nnext: docker compose -f docker-compose.yml -f docker-compose.remote.yml up -d --build")
    print("optional: add PRIVATE_COLLECTIONS=true to .env for per-person collections")


if __name__ == "__main__":
    main()
