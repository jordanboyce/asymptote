# Hosting Finn for testers — Cloudflare Tunnel + Access

**Finn has no login of its own.** Single-user mode — the default — assumes the
app is reachable only by you. Put that on a public URL and anyone with the link
has full read/write access to every client file in it.

This runbook puts Cloudflare Access in front, so each tester signs in with a
one-time code emailed to them and Finn sees a *cryptographically verified*
identity. No application code changes; all of it is configuration.

**What you get:** per-person sign-in, per-person revoke, an access log, and
isolated collections per tester. Free up to 50 users.

---

## The security model, in one paragraph

`cloudflared` makes an **outbound** connection to Cloudflare and listens on no
public port, so the origin has no inbound attack surface. Cloudflare
authenticates the user at the edge and forwards a signed JWT
(`Cf-Access-Jwt-Assertion`). Finn verifies that signature against your team's
public keys before believing anything about who is calling — which is what
makes this safe even if the origin later becomes reachable some other way.
Cloudflare also sends a plaintext `Cf-Access-Authenticated-User-Email` header;
Finn deliberately **ignores** it, because a plaintext header is forgeable by
anyone who can reach the origin directly. Cloudflare's own guidance is that
"validation of the header alone is not sufficient."

---

## Before you start

- A domain on Cloudflare (any plan, including free).
- A Cloudflare Zero Trust account — dash.cloudflare.com → **Zero Trust**. It
  asks for a **team name**; pick one and note it, you need it in step 5.
- `cloudflared` installed on the machine that will run Finn:
  - macOS: `brew install cloudflared`
  - Windows: `winget install --id Cloudflare.cloudflared`
  - Linux: [packages](https://pkg.cloudflare.com/)

---

## 1. Create the tunnel

Dashboard config is easier to change later than a local config file, so use it.

1. **Zero Trust** → **Networks** → **Tunnels** → **Create a tunnel**
2. Type **Cloudflared**, name it `finn`, save.
3. Copy the install command it shows. Run it on the machine hosting Finn.
   The token in it is a credential — treat it like a password.
4. Under **Public Hostnames**, add:
   - **Subdomain**: `finn` · **Domain**: your domain
   - **Service**: `HTTP` → `localhost:8000`
     *(match whatever `PORT` you run Finn on)*

Start Finn (`./run.sh` or `run.bat`) and confirm the tunnel shows **Healthy**.

At this point `https://finn.yourdomain.com` works — **and is wide open.** Do not
stop here. Do not upload anything real yet.

---

## 2. Turn on email one-time PIN

1. **Zero Trust** → **Integrations** → **Identity providers** → **Add new**
2. Choose **One-time PIN**. There is nothing to configure.

Testers will get a 6-digit code by email. Each code is **single-use and expires
in 10 minutes**, and requesting a new one invalidates the previous one.

> If your testers are at firms running Mimecast, Barracuda, or similar, have
> them allowlist `noreply@notify.cloudflare.com`. Some scanners *follow links
> in* or *consume* the code before the human sees it, which presents as "the
> code never works" and is maddening to debug from the other end.

---

## 3. Create the Access application

1. **Zero Trust** → **Access** → **Applications** → **Add an application**
2. Type **Self-hosted**. Name it `Finn`.
3. **Public hostname**: the `finn.yourdomain.com` from step 1.
4. Add a policy:
   - **Name**: `Testers` · **Action**: `Allow`
   - **Include** → **Emails** → list each tester's address
     *(or **Emails ending in** `@theirfirm.com` to admit a whole firm)*
5. Save.

Use **Emails**, not **Everyone** — an Allow/Everyone policy authenticates
people and then lets all of them in, which is not what it sounds like.

---

## 4. Get the AUD tag

**Zero Trust** → **Access** → **Applications** → `Finn` → **Configure** →
**Additional settings** → **Application Audience (AUD) Tag**. Copy the 64-character hex string.

This is what binds a token to *this* application. Without checking it, a token
minted for any other app in your account would be accepted by Finn.

---

## 5. Point Finn at it

In `.env`:

```bash
ENABLE_MULTI_USER=true
ACCESS_TEAM_DOMAIN=your-team-name          # or your-team-name.cloudflareaccess.com
ACCESS_AUD=<the 64-char AUD tag>
CORS_ALLOW_ORIGINS=https://finn.yourdomain.com
```

Leave `TRUST_PROXY_USER_HEADER=false`. It exists for non-Access proxies that
can only send a plain header, and it is strictly weaker.

### Move your existing data across

**Read this before you restart, or you will think your data is gone.**

Flipping `ENABLE_MULTI_USER=true` changes who you are. Everything you have
built so far is owned by the user `default`; from now on you arrive as
`you@example.com`, and Finn will correctly show you an empty app.

```bash
python scripts/reassign_owner.py --to you@example.com            # dry run
python scripts/reassign_owner.py --to you@example.com --apply    # writes, backs up first
```

Use the same address you will sign in with, lowercased. Then restart Finn.

---

## 6. Verify — including the part people skip

1. **Sign-in works.** Open `https://finn.yourdomain.com` in a private window.
   You should get a Cloudflare sign-in page, not Finn. Enter your email, then
   the code. You should land in Finn with your collections present.

2. **A stranger is refused.** Ask someone not on the policy to try. They should
   be stopped at Cloudflare and never reach Finn.

3. **The origin is not reachable directly.** This is the step that actually
   proves the setup, and it is the one everyone skips:

   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' http://<host-machine-ip>:8000/health
   ```

   From another machine on the network. **Anything other than a timeout or a
   connection refusal means Finn is exposed on your LAN, bypassing Access
   entirely.** Bind Finn to localhost (`HOST=127.0.0.1` in `.env`) — the tunnel
   reaches it locally and nothing else can.

4. **Identity is real.** Sign in and check the footer or `/api/user/me`. It
   should show your email, not `default`.

---

## 7. Adding and removing testers

**Zero Trust** → **Access** → **Applications** → `Finn` → **Configure** →
policy → edit the email list. Takes effect on their next sign-in.

Revoking is the same edit in reverse, plus **Zero Trust** → **Users** → select →
**Revoke sessions** if you want them out immediately rather than at expiry.

Each tester's collections are their own. To let one see another's, use Finn's
in-app share — do not put them on a shared login.

---

## 8. Troubleshooting

| Symptom | Cause |
|---|---|
| **Error 1016** at the hostname | Tunnel not running. `cloudflared tunnel info finn`; check the service is up on the host. |
| Finn returns **401** on every request after sign-in | `ACCESS_AUD` or `ACCESS_TEAM_DOMAIN` mismatch. Re-copy the AUD; confirm the team domain matches the sign-in URL. Finn logs the specific reason at WARNING. |
| **401** but Cloudflare sign-in worked | The request reached the origin without traversing Access. Re-run check 3 above. |
| Signed in, but **no collections** | The ownership migration in step 5 was skipped, or run with a different address than you sign in with. |
| Code email never arrives | Mail scanner consumed it. Allowlist `noreply@notify.cloudflare.com`. Note codes expire in 10 minutes and requesting a new one voids the old. |
| Everything works, then 401s after ~6 weeks | Should not happen — Finn refetches keys on an unknown key ID. If it does, restart Finn and file it as a bug. |

---

## 9. What this does and does not protect

**Does:** stops unauthenticated access; gives per-person identity, revoke, and
an access log; keeps the origin off the public internet; isolates each tester's
collections.

**Does not:**

- **Encrypt data at rest.** `./data` is plain files on the host. Full-disk
  encryption is your job.
- **Stop an authorized tester from exporting what they can see.** Access
  controls who gets in, not what they do once inside.
- **Change where AI requests go.** Chat still calls your configured provider
  under your key. Redaction still happens at that boundary — Access is
  orthogonal to it.
- **Make this a multi-tenant product.** It is one instance with per-user
  isolation, on your hardware. Hosting real client data for a firm that is not
  you is a different conversation (SOC 2, a BAA-equivalent, data residency).

**A caution worth repeating:** until you have run check 3 and are satisfied
with what the Boundary Report shows, prefer anonymized or sample data over live
client files.
