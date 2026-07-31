# Deploying Asymptote for a Team

Asymptote is a **shared team appliance**. Everyone who can reach it sees the
whole corpus — every collection, every document, every table, and the same
`/mcp` tool surface. There are no per-user permissions inside the app.

That is a deliberate design choice, and it makes deployment simple: the only
question you have to answer is *who can reach it*. This guide answers it.

## The security model in one paragraph

The app has no login of its own. Access is controlled at the edge by whatever
you put in front of it, and the app is bound so that the edge is the only route
in. Nothing else is required — no accounts to provision inside Asymptote, no
password resets, no per-user configuration.

| Setting | Default | What it controls |
|---|---|---|
| `HOST` | `127.0.0.1` | Which interfaces accept connections. Docker sets `0.0.0.0`; there the published port is the boundary. |
| `AUTH_PASSWORD` | empty | A shared secret required on every request except `/health`. Use it when the app stays reachable on a network your proxy does not exclusively own. |
| `CORS_ALLOW_ORIGINS` | empty | Which *other* web origins may read responses in a browser. Empty is correct unless a separate web app calls the API. |

The app warns at startup if it is bound to the network with no password, and
refuses to start with `ENABLE_MULTI_USER=true` (see
[Why there is no multi-user mode](#why-there-is-no-multi-user-mode)).

## Choose a deployment shape

### A. Just you, on your machine

The defaults. `HOST=127.0.0.1` means nothing outside your machine can connect,
so no password is needed.

```bash
docker compose up -d     # or: python main.py
```

### B. A team, via SSO — recommended

Put an identity-aware proxy in front and let it decide who gets in. Your
colleagues are already signed into Google/Okta/Entra, so for them access is
just opening the URL — no password to type, share, or rotate. You get
per-person revocation, MFA, and an access log without writing any code.

Good options: **Cloudflare Access**, **Tailscale** (with `tailscale serve`),
**oauth2-proxy**, **Authelia**, **Pomerium**.

The critical part is step 2 — the proxy must be the *only* route in. A proxy
you can walk around protects nothing.

**1. Keep Asymptote off the public network.** With compose, drop the `ports:`
mapping so the container is reachable only on the internal Docker network:

```yaml
services:
  asymptote:
    # ports:              # ← removed: no direct access from the host
    #   - "8473:8473"
    expose:
      - "8473"
    environment:
      - HOST=0.0.0.0      # inside the container; the network is the boundary
```

**2. Put the proxy on that network** and point it at `http://asymptote:8473`.
Only the proxy publishes a port.

**3. Verify the bypass is actually closed.** From another machine, try to reach
the app directly, not through the proxy:

```bash
curl -sS --max-time 5 http://<host>:8473/health && echo "REACHABLE — fix this"
```

That must fail. If it succeeds, the app is exposed and your SSO is decorative.

**Optional — belt and braces.** If the app must stay reachable on a network the
proxy does not exclusively own, set `AUTH_PASSWORD` and have the proxy inject
it, so requests that skip the proxy are rejected:

```
Authorization: Bearer <AUTH_PASSWORD>
```

Configure this on the proxy, not in a browser. If you set `AUTH_PASSWORD`
*without* injecting it, people get a Basic-auth popup on top of their SSO
login — two prompts for one door.

### C. A team, via private network

Tailscale, WireGuard, or a corporate VPN. The network is the boundary: bind to
the private interface and skip `AUTH_PASSWORD` entirely. Simplest option if
everyone is already on the VPN, though you get no per-request audit trail.

### D. Public URL with a shared password

`AUTH_PASSWORD` alone, no proxy. Browsers prompt natively (HTTP Basic, any
username); API and MCP clients send `Authorization: Bearer <password>`.

Fine for a demo or a short-lived deployment. Not great for a standing team
tool: one password everyone shares, no way to revoke one person, and no record
of who did what. Prefer **B**.

## MCP clients

`/mcp` is part of the same app, so it sits behind the same door — with one
wrinkle: **MCP clients authenticate as a token, not as a person.**

- With `AUTH_PASSWORD`, clients send `Authorization: Bearer <password>` and
  work normally.
- With an SSO proxy, a headless MCP client has no browser to complete the login
  in. Either use a proxy that issues service tokens (Cloudflare Access does), or
  reach the app over the private network from **C**.

Whatever the client, it gets the same full-corpus access as everyone else.

## Why there is no multi-user mode

There was an `ENABLE_MULTI_USER` flag. It filtered the collection *list* by
owner, which looked like per-user isolation but was not one: search, document
retrieval, chat, and every `/mcp` tool accept a `collection_id` and act on it
without an ownership check. Anyone who knew or guessed an 8-character
collection ID could read a collection that was hidden from their list.

Rather than leave a boundary that holds in the UI and not in the API, the flag
now refuses to start. Asymptote is honest about being a shared appliance:
**everyone who gets in, sees everything.** Scope access to a group that is
allowed to see the whole corpus, and run a second instance for material that
needs a different audience — separate instances are a real boundary in a way
that a half-enforced flag never was.

Making it real would mean threading `user_id` through every entry point that
takes a `collection_id` — search, documents, chat, and the MCP tool layer —
with an access check at each, plus per-user tokens so MCP connections map to a
person. That is a project, not a flag.

## Backups

Everything lives in the data directory (`./data` by default, `/app/data` in
Docker): uploaded documents, FAISS indexes, the SQLite metadata and config
databases. Back up that directory and you can restore the whole deployment.

Stop the container first, or snapshot the volume, so SQLite is not mid-write.

## Checklist before you hand out the URL

- [ ] Direct access to the app's port fails from another machine — only the
      proxy answers
- [ ] `AUTH_PASSWORD` set, or the proxy is provably the only route in
- [ ] TLS terminated at the proxy or platform (`https://`, not `http://`)
- [ ] `CORS_ALLOW_ORIGINS` empty, or an explicit origin list — never `*` on a
      deployment others can reach
- [ ] The people who can log in are all cleared to see **every document** in
      every collection
- [ ] The data directory is backed up
