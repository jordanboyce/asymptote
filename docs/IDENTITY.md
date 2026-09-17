# Identity: who a request is

Asymptote has two separate questions, and they are often confused:

- **Can this caller in at all?** `AUTH_PASSWORD`, or nothing on a closed
  network. See [DEPLOYMENT.md](DEPLOYMENT.md).
- **Who is this caller?** `IDENTITY_PROVIDER`. This page.

The second one only matters when something acts on it. Two things do:
`PRIVATE_COLLECTIONS` (per-person ownership and sharing) and the audit trail
(`actor` on every recorded event). Without a verified identity the app is a
shared appliance: everyone who gets in sees the whole corpus, which is the
right model for a team and the wrong one for an agency.

The app **refuses to start** on an identity configuration it cannot enforce.
That is deliberate: a boundary that is advertised but not enforced is worse
than no boundary, because people put things behind it.

## Pick a provider

| `IDENTITY_PROVIDER` | Use when | Identity comes from |
| --- | --- | --- |
| *(empty)* | Team appliance, or you already set `CF_ACCESS_*` | Auto: Cloudflare if configured, else nobody |
| `cloudflare_access` | Internet-facing, via Cloudflare Tunnel | `Cf-Access-Jwt-Assertion` |
| `oidc` | **On-prem / air-gapped.** You run Keycloak, Entra ID, Okta, PingFederate, ADFS | `Authorization: Bearer <JWT>` |
| `trusted_header` | An authenticating proxy already fronts the app (mTLS, SPNEGO/Kerberos, a site SSO proxy) | A header that proxy sets |

Leaving `IDENTITY_PROVIDER` empty keeps every existing Cloudflare deployment
working unchanged — `CF_ACCESS_TEAM_DOMAIN` + `CF_ACCESS_AUD` still select
Cloudflare on their own.

---

## `oidc` — your own identity provider

The on-prem answer. Tokens your IdP already issues become identities here.

```bash
IDENTITY_PROVIDER=oidc
OIDC_ISSUER=https://sso.agency.gov/realms/main
OIDC_AUDIENCE=asymptote               # the client id / API audience
OIDC_IDENTITY_CLAIM=email             # default; preferred_username and sub are fallbacks
PRIVATE_COLLECTIONS=true
```

**`OIDC_AUDIENCE` is not optional.** Without it, any token your IdP ever
issued — for any application in the realm — would be accepted here. The app
refuses to start without it.

What is verified on every request: the signature against the issuer's JWKS,
the issuer, the audience, and the expiry. Asymmetric algorithms only
(`RS*`/`ES*`/`PS*`) — an `HS256` token is refused even when it names a known
key id, because otherwise the published JWKS modulus becomes a signing key
anyone can use.

### Narrowing admission by claim

```bash
OIDC_REQUIRED_CLAIMS=groups=asymptote-users
```

Comma-separated `name=value` pairs, all required. Each matches a scalar
claim or membership in a list claim, so one IdP group can gate the whole
deployment. Have your IdP release that claim to this client.

### Air-gapped: skip discovery

By default the issuer's `/.well-known/openid-configuration` is fetched to
find the JWKS URL. If that is unreachable, name the key set directly:

```bash
OIDC_JWKS_URL=https://sso.internal/realms/main/protocol/openid-connect/certs
```

Keys are cached for an hour and refreshed once on an unknown key id, so IdP
key rotation needs no restart. If the JWKS cannot be fetched at all, requests
are **refused**, never admitted.

### Certificates

An internal IdP usually presents a private CA certificate. Mount the CA at
`/certs/ca` — the container installs it into both the system store and
certifi's bundle at start up. See [ONPREM.md](ONPREM.md).

### What the agent clients do

MCP clients (Claude Code, Codex) still use personal MCP tokens from
Settings → MCP — those are minted by a person who is already signed in, and
they carry that person's identity. You do not need to hand a browser OIDC
token to an agent. Connector-style clients (Claude Desktop, claude.ai,
ChatGPT) need OAuth, which this release does not implement; see
[ARCHITECTURE.md](ARCHITECTURE.md).

---

## `trusted_header` — an authenticating proxy

For sites whose front door is already a proxy that authenticates: an mTLS
terminator passing the certificate subject, a Kerberos/SPNEGO front end, or
an existing SSO proxy.

```bash
IDENTITY_PROVIDER=trusted_header
TRUSTED_HEADER_NAME=X-Forwarded-User
HOST=127.0.0.1                 # the guard: your proxy is the only route in
PRIVATE_COLLECTIONS=true
```

The header is forgeable by anything that can open a socket to the app, so
one of two guards is **mandatory** — the app will not start otherwise:

1. **`HOST` is loopback.** Only a process on the host can connect, so the
   proxy is the only route in. Simplest, and correct for the usual
   nginx/Apache-in-front shape.
2. **`TRUSTED_HEADER_SECRET`.** The proxy proves the identity with
   HMAC-SHA256 over the header value, hex-encoded, in
   `TRUSTED_HEADER_SIGNATURE_NAME`. Holds regardless of network path — use
   it when the proxy is on another host.

```bash
TRUSTED_HEADER_SECRET=<32+ random bytes>
TRUSTED_HEADER_SIGNATURE_NAME=X-Forwarded-User-Signature
```

The signature covers the identity value exactly:

```
signature = hex( HMAC-SHA256( TRUSTED_HEADER_SECRET, "analyst@agency.gov" ) )
```

Signing the value — not just sending a shared secret — is what stops a proxy
bug, or anything that learns the secret from a log, from claiming to be
someone else.

### Why `TRUSTED_HEADER_PROXIES` is not a guard by itself

```bash
TRUSTED_HEADER_PROXIES=10.8.0.0/24    # defence in depth, never the only guard
```

uvicorn's proxy-header handling rewrites the peer address from
`X-Forwarded-For` for connections from `FORWARDED_ALLOW_IPS` (127.0.0.1 by
default). So the address this check sees is not always the real socket peer,
and a check the caller can influence is not a boundary. The app enforces
this: a configuration whose only guard is a CIDR list refuses to start.

### mTLS

Terminate TLS at the proxy, then pass the subject:

```nginx
# nginx
proxy_set_header X-Forwarded-User $ssl_client_s_dn_cn;
proxy_set_header X-Forwarded-User-Signature $hmac;   # njs/Lua, if not loopback
```

Make sure the proxy **strips any inbound `X-Forwarded-User`** before setting
its own. `proxy_set_header` replaces rather than appends, which covers the
usual case.

---

## Auditing what agents read

Writes have always been audited. Reads over MCP are not, unless you ask:

```bash
MCP_AUDIT_TOOL_CALLS=true
```

Then every MCP tool call appends an `mcp.tool_call` event: the tool, the
verified identity, the personal token id, the collection, the documents
touched and how many results came back. Export it from Admin → Audit.

Deliberately **not** recorded: passage text, queries, and result contents.
The audit table is read by more people and kept longer than the corpus it
describes, so it must not become a second copy of it.

Retention follows `AUDIT_RETENTION_DAYS` (0 = forever). Expect roughly one
row per tool call — an agent turn is typically several.

---

## Checklist

- [ ] `IDENTITY_PROVIDER` set (or `CF_ACCESS_*` for the Cloudflare default)
- [ ] The app starts — a refusal here names exactly what is missing
- [ ] `OIDC_AUDIENCE` is this app's client id, not a shared or wildcard value
- [ ] `PRIVATE_COLLECTIONS=true` if people should not see each other's sources
- [ ] For `trusted_header`: loopback bind or HMAC secret, and the proxy
      strips inbound copies of the identity header
- [ ] `MCP_AUDIT_TOOL_CALLS=true` where reads must be accountable
- [ ] `ADMIN_EMAILS` lists identities as this provider reports them (the
      email claim, the certificate CN — whatever `OIDC_IDENTITY_CLAIM` or the
      proxy header actually contains)
