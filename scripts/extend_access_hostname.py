"""Extend the existing Cloudflare Access apps to a second hostname.

Use this when the app moves to a new public name (asymptote.cyberlion.dev ->
clio.cyberlion.dev) and the Access apps should cover both. It edits the SAME
application ids in place, so the AUD tags in .env (CF_ACCESS_AUD) stay valid;
creating new apps would rotate them and break JWT verification.

The apps endpoint has no PATCH for API-token auth (10405), so this is a full
PUT that echoes the current configuration back with self_hosted_domains
extended and the existing policies re-attached by id (omitting them detaches
them). Idempotent: an app that already lists the new hostname is skipped.

    python scripts/extend_access_hostname.py            # asymptote -> clio
    python scripts/extend_access_hostname.py --old a.example.com --new b.example.com
    python scripts/extend_access_hostname.py --dry-run

Reads CLOUDFLARE_API_TOKEN (and optionally CF_ACCOUNT_ID) from the environment
or .env in the current directory.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.cloudflare.com/client/v4"
READONLY = {"id", "uid", "aud", "created_at", "updated_at", "policies", "destinations"}


def load_env():
    if os.path.exists(".env"):
        for line in open(".env", encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def cf(token, method, path, body=None):
    req = urllib.request.Request(
        API + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        data = json.load(e)
    if not data.get("success"):
        sys.exit(f"{method} {path} failed: {data.get('errors')}")
    return data["result"]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--old", default="asymptote.cyberlion.dev")
    ap.add_argument("--new", default="clio.cyberlion.dev")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    load_env()
    token = os.environ.get("CLOUDFLARE_API_TOKEN") or sys.exit("CLOUDFLARE_API_TOKEN is not set")
    account = os.environ.get("CF_ACCOUNT_ID", "71a9952ea49acb57979b8707f6666463")

    apps = cf(token, "GET", f"/accounts/{account}/access/apps?per_page=100")
    touched = 0
    for app in apps:
        hosts = list(app.get("self_hosted_domains") or [])
        if not any(h.startswith(args.old) for h in hosts):
            continue
        extended = list(hosts)
        for h in hosts:
            candidate = args.new + h[len(args.old):] if h.startswith(args.old) else h
            if candidate not in extended:
                extended.append(candidate)
        if extended == hosts:
            print(f"{app['name']}: already covers {args.new}")
            continue
        body = {k: v for k, v in app.items() if k not in READONLY and v is not None}
        body["self_hosted_domains"] = extended
        body["policies"] = [
            {"id": p["id"], "precedence": p["precedence"]}
            for p in sorted(app.get("policies") or [], key=lambda p: p["precedence"])
        ]
        print(f"{app['name']} ({app['id'][:8]}): {hosts} -> {extended}")
        if args.dry_run:
            continue
        res = cf(token, "PUT", f"/accounts/{account}/access/apps/{app['id']}", body)
        if res.get("aud") != app.get("aud"):
            sys.exit(f"  AUD changed on {app['name']} - update CF_ACCESS_AUD in .env!")
        kept = [(p["name"], p["decision"], p["precedence"]) for p in res.get("policies") or []]
        print(f"  ok - aud unchanged, hosts={res['self_hosted_domains']}, policies={kept}")
        touched += 1
    print(f"done: {touched} app(s) updated" + (" (dry run)" if args.dry_run else ""))


if __name__ == "__main__":
    main()
