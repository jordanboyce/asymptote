# Remote Access: Asymptote behind Cloudflare Tunnel + Access

This is the concrete recipe for deployment shape **B** from
[DEPLOYMENT.md](DEPLOYMENT.md): host Asymptote on any Docker machine (a home
box, a VPS, a lab server) and reach it securely from anywhere at
`https://asymptote.<your-domain>` — browser access via SSO, headless MCP
clients via service tokens. The host needs **no open inbound ports**; the
tunnel dials out to Cloudflare.

Three layers, so no single mistake exposes the corpus:

1. **Cloudflare Access** authenticates every request at the edge (your
   identity provider for browsers, service tokens for agents).
2. **The tunnel** is the only route in — the compose override publishes no
   port on the host.
3. **`AUTH_PASSWORD`** makes the app itself reject anything that somehow
   bypasses the edge.

## One-time Cloudflare setup (~15 minutes, needs your Cloudflare login)

Prerequisite: your domain is on Cloudflare (free plan is fine; Zero Trust
free tier covers up to 50 users).

> **Scripted alternative:** [scripts/provision_cloudflare.py](../scripts/provision_cloudflare.py)
> performs steps 1–3 (plus the DNS record and secrets bookkeeping) in one
> idempotent run, given an API token with *Cloudflare Tunnel: Edit*,
> *Access: Apps and Policies: Edit*, *Access: Service Tokens: Edit*, and
> *Zone DNS: Edit* permissions. The dashboard steps below are the manual
> equivalent.

1. **Create the tunnel.** Zero Trust dashboard → *Networks → Tunnels →
   Create a tunnel* (Cloudflared connector). Name it `asymptote`. Copy the
   token from the install command — that's `TUNNEL_TOKEN`.
2. **Route the hostname.** In the tunnel's *Public Hostname* tab, add
   `asymptote.<your-domain>` → service `http://asymptote:8473`
   (the compose service name; cloudflared runs on the same Docker network).
3. **Gate it with Access.** Zero Trust → *Access → Applications → Add an
   application* (Self-hosted), domain `asymptote.<your-domain>`.
   - **Browser policy** (Allow): include your email(s) or your IdP group.
   - **MCP/agent policy** (Service Auth): create a service token under
     *Access → Service Auth → Service Tokens*, then add a policy with action
     **Service Auth** that includes that token.

## Deploy (on the Docker host)

```bash
# .env next to docker-compose.yml:
#   TUNNEL_TOKEN=<from step 1>
#   AUTH_PASSWORD=<long random string, e.g. `openssl rand -base64 33`>
#   MCP_ALLOWED_HOSTS=asymptote.<your-domain>
docker compose -f docker-compose.yml -f docker-compose.remote.yml up -d --build
```

`MCP_ALLOWED_HOSTS` matters: the MCP SDK ships DNS-rebinding protection that
only trusts localhost `Host` headers, and the tunnel forwards the public
hostname. Without this setting the UI works but every `/mcp` request is
rejected with `Invalid Host header`.

## Verify the door is actually closed

```bash
# 1. Direct access must FAIL (no published port):
curl -sS --max-time 5 http://<host-ip>:8473/health && echo "REACHABLE — fix this"

# 2. The public URL without credentials must redirect to the Access login,
#    not reach the app:
curl -sS -o /dev/null -w "%{http_code}\n" https://asymptote.<your-domain>/api/collections
# expect 302 (Access login), never 200/401 from the app itself

# 3. With the service token + app password, the MCP handshake must succeed
#    (the token is scoped to /mcp — it will NOT open /health or the UI):
curl -sS -X POST https://asymptote.<your-domain>/mcp/ \
  -H "CF-Access-Client-Id: <token-id>.access" \
  -H "CF-Access-Client-Secret: <token-secret>" \
  -H "Authorization: Bearer <AUTH_PASSWORD>" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"verify","version":"0"}}}'
# expect a JSON-RPC result with "serverInfo": {"name": "Asymptote", ...}
```

## Pointing MCP clients at it

Headless clients authenticate with the service token **and** the app
password. For clients that support custom headers (Claude Code, most MCP
SDKs), configure the `/mcp` endpoint with:

```
URL:     https://asymptote.<your-domain>/mcp/
Headers: CF-Access-Client-Id: <token-id>.access
         CF-Access-Client-Secret: <token-secret>
         Authorization: Bearer <AUTH_PASSWORD>
```

For example, from Claude Code:

```bash
claude mcp add --transport http asymptote https://asymptote.<your-domain>/mcp/ \
  -H "CF-Access-Client-Id: <token-id>.access" \
  -H "CF-Access-Client-Secret: <token-secret>" \
  -H "Authorization: Bearer <AUTH_PASSWORD>"
```

Rotate a leaked service token in the Zero Trust dashboard (per-client
revocation is the reason to prefer tokens over sharing the password alone).

## Optional: private collections

With the JWT trust settings the script writes (`CF_ACCESS_TEAM_DOMAIN`,
`CF_ACCESS_AUD`), you can add `PRIVATE_COLLECTIONS=true` to `.env` and
restart: new collections become private to whoever created them (your Access
SSO email), shareable via read/readwrite links, while everything that already
existed stays in a team tier everyone sees. The MCP service token is an
identity too (`asymptote-mcp`) — share a collection to that name to grant
agents access; to accept on the token's behalf, call the accept endpoint with
its credentials:

```bash
curl -sS -X POST https://asymptote.<your-domain>/api/shares/<share-token>/accept \
  -H "CF-Access-Client-Id: <token-id>.access" \
  -H "CF-Access-Client-Secret: <token-secret>"
```

Note the service token is admitted only by the `/mcp` Access app; the accept
endpoint sits under the root app, so this call works only after you add a
Service Auth policy for the token to the root application as well (Zero Trust
→ Access → Applications → Asymptote → Policies). If you'd rather not widen
the root app, skip sharing to agents and keep what they need in the team
tier.

Full semantics: [DEPLOYMENT.md](DEPLOYMENT.md#private-collections).

## Browser access

Just open `https://asymptote.<your-domain>` — Access sends you through your
IdP once, then sets its own cookie. Because Access already authenticated the
person, you can have Access inject the app password so nobody types it:
in the Access application's settings, add an HTTP request header
`Authorization: Bearer <AUTH_PASSWORD>`. Without that, browsers see one
extra native password prompt (any username) after SSO.

## Alternative: Tailscale

If everything that needs Asymptote (laptops, phone, agent hosts) can join
your tailnet, `tailscale serve https / http://localhost:8473` on the host is
simpler: no public hostname at all, TLS handled for you, and `AUTH_PASSWORD`
optional. The trade-off is that nothing outside the tailnet — e.g. a cloud
agent — can ever reach it. The Cloudflare shape above is the default for
"from anywhere".
