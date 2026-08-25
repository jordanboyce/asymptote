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
docker compose -f docker-compose.yml -f docker-compose.remote.yml up -d --build
```

## Verify the door is actually closed

```bash
# 1. Direct access must FAIL (no published port):
curl -sS --max-time 5 http://<host-ip>:8473/health && echo "REACHABLE — fix this"

# 2. The public URL without credentials must redirect to the Access login,
#    not reach the app:
curl -sS -o /dev/null -w "%{http_code}\n" https://asymptote.<your-domain>/api/collections
# expect 302 (Access login), never 200/401 from the app itself

# 3. With a service token it must work:
curl -sS https://asymptote.<your-domain>/health \
  -H "CF-Access-Client-Id: <token-id>.access" \
  -H "CF-Access-Client-Secret: <token-secret>"
```

## Pointing MCP clients at it

Headless clients authenticate with the service token **and** the app
password. For clients that support custom headers (Claude Code, most MCP
SDKs), configure the `/mcp` endpoint with:

```
URL:     https://asymptote.<your-domain>/mcp
Headers: CF-Access-Client-Id: <token-id>.access
         CF-Access-Client-Secret: <token-secret>
         Authorization: Bearer <AUTH_PASSWORD>
```

For example, from Claude Code:

```bash
claude mcp add --transport http asymptote https://asymptote.<your-domain>/mcp \
  -H "CF-Access-Client-Id: <token-id>.access" \
  -H "CF-Access-Client-Secret: <token-secret>" \
  -H "Authorization: Bearer <AUTH_PASSWORD>"
```

Rotate a leaked service token in the Zero Trust dashboard (per-client
revocation is the reason to prefer tokens over sharing the password alone).

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
