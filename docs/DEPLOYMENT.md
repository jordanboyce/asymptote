# Deploying Asymptote for a Team

By default Asymptote is a **shared team appliance**. Everyone who can reach it
sees the whole corpus — every collection, every document, every table, and the
same `/mcp` tool surface. There are no per-user permissions inside the app,
which makes deployment simple: the only question you have to answer is *who
can reach it*. This guide answers it.

Deployments fronted by Cloudflare Access can additionally opt into
[private collections](#private-collections) — per-person ownership and
sharing, enforced at every entry point against the verified Access identity.

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
[Private collections](#private-collections) for the supported replacement).

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

Whatever the client, it gets the same full-corpus access as everyone else —
unless [private collections](#private-collections) are on, where a service
token's `common_name` is an identity that collections can be shared to, and a
password-authenticated client reaches team collections only.

## Private collections

`PRIVATE_COLLECTIONS=true` turns per-person ownership on. It exists because
its predecessor — an `ENABLE_MULTI_USER` flag that filtered only the
collection *list* while search, documents, chat, and every `/mcp` tool acted
on any `collection_id` unchecked — advertised an isolation boundary without
being one. That flag still refuses to start; this mode is the real version of
what it pretended to be.

**It requires Cloudflare Access** (`CF_ACCESS_TEAM_DOMAIN` + `CF_ACCESS_AUD`,
deployment shape **B**). Ownership enforced against an identity anyone can
forge is worthless, so the app refuses to start with the flag on and no
verified identity source configured. The Access JWT the edge attaches to every
request is the identity: a person's email for SSO logins, a service token's
name for MCP clients.

What changes when it is on:

- **Every collection has an owner** — the identity that created it. A
  collection is visible only to its owner until shared: the collection list,
  search, chat retrieval (including "search all collections"), document
  serving, and every `/mcp` tool all enforce it. An inaccessible collection
  id answers exactly like a missing one, so ids cannot be probed.
- **Everything that existed before stays shared.** Collections owned by
  `default` — all pre-existing data, including the default collection — form
  a *team* tier that everyone can see and write. Flipping the flag on changes
  nothing about existing data; new collections are simply private to their
  creators.
- **Sharing is built in.** An owner creates a share link (read or readwrite,
  optional expiry) from the collection's ⋮ menu; the recipient pastes the
  token in the same dialog to accept. Shares are revocable, and deletion
  stays owner-only.
- **MCP clients map to identities.** An Access service token authenticates
  the client and its `common_name` is its identity — share a collection to
  that name to grant an agent access. A client authenticating with
  `AUTH_PASSWORD` instead has no identity and sees team collections only, as
  does any password-authenticated browser session.

Two deliberate limits, so the boundary stays honest: password callers cannot
own or accept anything (no identity), and if you need a harder wall than
application-level checks — different compliance regimes, different tenants —
run a second instance. Separate instances remain a stronger boundary than any
in-app flag.

## Capacity & scaling

Asymptote is **one process by design**: the FAISS indexes, background-job
registry, and SSE progress queues all live in the process's memory, so
`uvicorn --workers N` or multiple replicas would silently diverge. Scale
**vertically** (more CPU/RAM on one host), and run a **second independent
instance** when you need a harder wall — different tenants, different
compliance regimes. `DB_BACKEND=postgresql` moves only the app metadata
database; vectors and per-collection stores stay on local disk, so it is
neither HA nor a path to replicas.

The comfortable envelope is an org appliance: **roughly 10–50 people** on
an always-on host. Two shared chokepoints define it — the request
threadpool (~40 slots; a chat turn holds one for its whole agent loop, up
to ~11 provider round-trips) and a single embedding lock every query and
indexing batch passes through. The identity layer agrees: Cloudflare Zero
Trust is free to 50 seats.

What protects the deployment when many people share it:

| Knob | Default | What it does |
|---|---|---|
| `RATE_LIMIT_CHAT_PER_MINUTE` | 6 | Per-identity chat requests/min |
| `RATE_LIMIT_SEARCH_PER_MINUTE` | 30 | Per-identity searches/min |
| `RATE_LIMIT_DEFAULT_PER_MINUTE` | 120 | Everything else under `/api` |
| `CHAT_DAILY_TOKEN_BUDGET` | 0 (off) | Provider tokens one identity may spend on chat per UTC day. Cached answers stay free once capped. |
| `MAX_CONCURRENT_INDEX_JOBS` | 2 | Indexing jobs across all collections |
| `SEARCH_HISTORY_RETENTION_DAYS` | 30 | Search log (stores result snippets) |
| `USAGE_RETENTION_DAYS` | 180 | Per-turn chat usage rows |

Identity for the limits is the verified Cloudflare Access email when
present, else the client IP (`CF-Connecting-IP` behind the tunnel). A
**429 response** means "you, specifically, are over a limit — wait the
`Retry-After` seconds," not that the server is down; the UI says so. With
`ADMIN_EMAILS` set, the Admin tab shows per-person spend, live process
stats, and the rate-limit counters; the same data is at
`/api/admin/usage` and `/api/admin/stats`.

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
      every collection — or `PRIVATE_COLLECTIONS=true` is on behind Cloudflare
      Access and the team tier holds nothing sensitive
- [ ] The data directory is backed up
