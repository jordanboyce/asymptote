# Getting Finn in front of users — hosting runbook

Step-by-step for standing Finn up somewhere your testers can reach it, with
authentication in front. Read §0 first; it decides which of the rest you do.

- **[§0 Which stack](#0-which-stack)** — and why Firebase is not it
- **[§1 Path A: your machine + Cloudflare Tunnel](#1-path-a--your-own-machine--cloudflare-tunnel)** — cheapest, most private, 30 min
- **[§2 Path B: Railway + Cloudflare Access](#2-path-b--railway--cloudflare-access)** — always-on, ~90 min
- **[§3 Cloudflare Access setup](#3-cloudflare-access-setup)** — required for both paths
- **[§4 Verification](#4-verification--do-not-skip-this)** — the checks that catch a broken deploy
- **[§5 Managing testers](#5-managing-testers)**
- **[§6 Troubleshooting](#6-troubleshooting)**
- **[§7 What you are taking on](#7-what-you-are-taking-on)**

---

## 0. Which stack

### Firebase: don't

Firebase is the wrong shape for this, and it's worth being specific about why
so it doesn't come back up later:

| Finn needs | Firebase offers |
|---|---|
| A long-running Python process | Hosting serves static files; Functions are short-lived JS/Python handlers |
| ~4–5 GB image (torch, spaCy, FAISS, Whisper) | Functions cap far below this; cold starts would be minutes |
| A persistent writable disk for `./data` | No filesystem — you'd rewrite storage against GCS |
| A local FAISS index held in memory | Nothing to hold it between invocations |

The nearest Google product that *could* run Finn is **Cloud Run** (GCP, not
Firebase), and even there the disk is ephemeral — you'd need GCS or Filestore
and a storage-layer rewrite. That's weeks of work to reach where Railway gets
you in an afternoon.

**The one case for Firebase:** if you wanted in-app email/password login
instead of Cloudflare Access, Firebase Auth is a reasonable identity provider.
But Finn already verifies Cloudflare Access JWTs
([services/access_auth.py](services/access_auth.py)), so that's rebuilding
something you have. Skip it.

### Railway vs. your own machine

| | Path A — your machine | Path B — Railway |
|---|---|---|
| Cost | £0 | ~$10–25/mo (see §2.6) |
| Setup | ~30 min | ~90 min |
| Uptime | Your machine must stay on | Always on |
| Where client data lives | Your hardware | Railway's (US/EU region) |
| Fits the local-first pitch | Yes | Needs a caveat — §7 |

**Start with Path A.** It's faster, free, keeps data on hardware you own, and
proves the product before you pay for anything. Move to Path B when leaving
your machine on becomes the annoying part.

Both paths use Cloudflare Access for authentication (§3). Finn has no login of
its own — do not put it on a public URL without this.

---

## 1. Path A — your own machine + Cloudflare Tunnel

Full detail is in [DEPLOYMENT.md](DEPLOYMENT.md); this is the summary.

1. Install `cloudflared` (`brew install cloudflared` / `winget install --id Cloudflare.cloudflared`).
2. **Zero Trust → Networks → Tunnels → Create** → type Cloudflared → name `finn`.
3. Run the install command it gives you on the host machine.
4. **Public Hostnames** → `finn.yourdomain.com` → `HTTP` → `localhost:8000`.
5. In `.env`, set `HOST=127.0.0.1` so nothing on your LAN can reach Finn
   around the tunnel.
6. Do §3 (Access), then §4 (verify).

Then jump to §3.

---

## 2. Path B — Railway + Cloudflare Access

The architecture matters here. **Two services in one Railway project:**

```
  Internet → Cloudflare Access (auth) → Cloudflare Tunnel
                                             │
                              Railway project│
                              ┌──────────────┴───────────────┐
                              │  cloudflared     finn        │
                              │  (service B) →   (service A) │
                              │                  + volume     │
                              └──────────────────────────────┘
                                 no public Railway domain
```

**Why not just give Finn a Railway domain and point Cloudflare at it?** Because
the `*.up.railway.app` URL keeps working and bypasses Access entirely. You'd
have authentication on the front door and an unlocked back one. Running
`cloudflared` inside the project and never generating a public domain closes
that off — Railway's edge has nothing to route.

### 2.0 If you deleted a previous Railway project

Deleting the project does not clean up what pointed *at* it. Before you start,
clear the leftovers or you'll debug a tunnel that resolves to nothing:

- **DNS**: in Cloudflare DNS, delete any `CNAME` / `TXT` records pointing at
  the old `*.up.railway.app` host.
- **Cloudflare tunnel**: if you're reusing an existing tunnel, its **Public
  Hostname** still points at the old service's internal address. It must be
  updated in §2.4 — a stale `finn.railway.internal` looks identical to a
  misconfigured new one.
- **Access application**: the app in Zero Trust survives and is fine to reuse.
  Keep its AUD tag; you'll need it in §3.4 and it hasn't changed.
- **Volume data is gone.** Deleting a project deletes its volumes. Nothing to
  recover, and nothing to migrate in §2.5.

### 2.1 Create the project and the Finn service

The repo now carries [railway.json](railway.json), so the build method,
healthcheck path, and restart policy configure themselves — Railway reads it on
first deploy and it overrides the dashboard. You do not need to set those by
hand.

1. Sign in at [railway.com](https://railway.com) → **New Project** →
   **Deploy from GitHub repo** → pick your Finn repo.
2. Railway reads `railway.json`, sees `"builder": "DOCKERFILE"`, and builds.
   **The first build takes 15–25 minutes** — it installs torch, downloads
   `en_core_web_lg` (~560 MB) and the embedding model. This is normal, and
   later builds are cached. The healthcheck timeout is set to 600s because
   loading the embedding model into RAM makes first boot slow.
3. Settings → rename the service to `finn`. **Do this before §2.4** — the
   internal DNS name follows the service name, and renaming later breaks the
   tunnel's hostname.
4. **Settings → Networking → do NOT generate a domain.** If one was created
   automatically, delete it.

### 2.2 Add the volume

Without this, every redeploy wipes every uploaded document and index.

1. Command palette (`⌘K` / `Ctrl-K`) → **New Volume** → attach to `finn`.
2. **Mount path: `/app/data`** — exactly this. Railway puts your app in
   `/app`, and Finn's `DATA_DIR` is `/app/data` per the Dockerfile. A volume
   mounted anywhere else silently persists nothing.
3. Size: start at 10 GB.

Two things the docs are quiet about and which will bite:

- **Volumes mount at container start, not at build.** Anything written during
  the build lives in the image, not the volume. Finn's models are baked into
  the image on purpose for this reason.
- **Do not scale this service past 1 replica.** Finn's SQLite databases and
  FAISS indexes assume a single writer. Two replicas on one volume will
  corrupt them.

### 2.3 Set environment variables

Railway service → **Variables** → paste as raw editor:

```bash
# ── required ──────────────────────────────────────────────────────────
DATA_DIR=/app/data
ENABLE_MULTI_USER=true
ACCESS_TEAM_DOMAIN=your-team-name
ACCESS_AUD=                          # fill in after §3.4
CORS_ALLOW_ORIGINS=https://finn.yourdomain.com

# ── AI, so testers don't need their own key ───────────────────────────
# NOTE: this is an *Ollama Cloud* key, not Anthropic. It is the only
# server-side key Finn reads. See the table below.
FALLBACK_API_KEY=
FALLBACK_MODEL=gemma4:31b

# ── strongly recommended ──────────────────────────────────────────────
ENABLE_PII_REDACTION=true            # default is already true; set it explicitly
RESEND_API_KEY=                      # else the in-app "Report issue" button 503s
FEEDBACK_EMAIL_TO=jordan.boyce@cyberlion.dev
```

Do **not** set `PORT` — Railway injects it and the Dockerfile now honours it.
Setting it by hand is a common cause of the 502 in §6.

> **There is no `ANTHROPIC_API_KEY` setting.** Finn's only server-side
> credential is `FALLBACK_API_KEY`, and it routes through **Ollama Cloud**
> ([main.py:4695](main.py#L4695)) — any advisor-selected model is deliberately
> stripped, since a `claude-…` id would 404 against ollama.com. Because
> `Settings` is configured with `extra="ignore"`, an `ANTHROPIC_API_KEY`
> variable is silently discarded: no error, no AI, no clue why.
>
> **To put testers on Anthropic instead**, there is no env var — each tester
> pastes their own key in Settings, which multi-user mode stores per person.
> That reintroduces the paste step `FALLBACK_API_KEY` exists to remove, so for
> a tester pilot the Ollama Cloud path is the intended one.

> If your Railway environment was created before **2025-10-16**, its private
> network is IPv6-only and you must also set `HOST=::`. Newer environments do
> both stacks and the default `0.0.0.0` is fine. Symptom if you get this
> wrong: `cloudflared` resolves `finn.railway.internal` and then can't
> connect, and the tunnel never turns healthy.

### 2.4 Add the cloudflared service

1. Create the tunnel first: **Zero Trust → Networks → Tunnels → Create** →
   Cloudflared → name it `finn`. On the install screen, copy the **token**
   (the long string after `--token`). Don't run the installer anywhere.
2. Back in Railway: **New → Empty Service**, name it `cloudflared`.
3. **Settings → Source → Docker Image**: `cloudflare/cloudflared:latest`
4. **Settings → Deploy → Start Command**:
   ```
   tunnel --no-autoupdate run --token $TUNNEL_TOKEN
   ```
5. **Variables** → `TUNNEL_TOKEN` = the token from step 1.
6. Back in the Cloudflare tunnel's **Public Hostnames**, add:
   - **Subdomain** `finn`, **Domain** your domain
   - **Service**: `HTTP` → `finn.railway.internal:8000`

   Use the *service name* you set in 2.1. Railway's internal DNS is
   `SERVICE_NAME.railway.internal`, and it resolves only inside the project.

The tunnel should go **Healthy** within a minute of both services deploying.

### 2.5 Migrate ownership

You enabled multi-user mode, which changes who you are — your existing
collections belong to `default` and you'll now arrive as your email. On a
fresh Railway volume there's nothing to migrate. If you copied a `data/`
directory up, run:

```bash
python scripts/reassign_owner.py --to you@example.com --apply
```

### 2.6 What it will cost

Finn is not a small container. Budget realistically:

| | |
|---|---|
| Image size | ~4–5 GB (torch, spaCy `lg`, FAISS, Whisper) |
| Idle RAM | ~1.5–2.5 GB once models load |
| RAM while transcribing | add ~1–2 GB for Whisper |
| Volume | 10 GB |

Hobby ($5/mo, includes $5 usage) will likely run **$10–25/mo** for one
always-on service of this size plus the tiny `cloudflared` one. If you don't
need meeting transcription in the hosted build, dropping `faster-whisper` from
`requirements.txt` is the single biggest saving.

---

## 3. Cloudflare Access setup

Required for both paths. This is what actually authenticates people.

### 3.1 Enable email one-time PIN

**Zero Trust → Integrations → Identity providers → Add new → One-time PIN.**
Nothing to configure.

Testers get a 6-digit code by email. Each is single-use and expires in 10
minutes; requesting a new one voids the previous.

> Testers at firms running Mimecast/Barracuda should allowlist
> `noreply@notify.cloudflare.com`. Some scanners consume the code before the
> human sees it — presents as "the code never works."

### 3.2 Create the application

**Zero Trust → Access → Applications → Add an application → Self-hosted.**

- **Name**: `Finn`
- **Public hostname**: `finn.yourdomain.com` (the one from §1.4 or §2.4)

### 3.3 Add the policy

- **Name** `Testers`, **Action** `Allow`
- **Include → Emails** → each tester's address
  (or **Emails ending in** `@theirfirm.com` for a whole firm)

Use **Emails**, not **Everyone**. An Allow/Everyone policy authenticates
people and then admits all of them, which is not what it sounds like.

### 3.4 Copy the AUD tag

**Applications → Finn → Configure → Additional settings → Application
Audience (AUD) Tag.** 64 hex characters.

Put it in `ACCESS_AUD` — `.env` for Path A, Railway variables for Path B, then
restart. This is what binds a token to *this* app; without the check, a token
minted for any other app in your account would be accepted.

---

## 4. Verification — do not skip this

Four checks. Three are obvious and one is the one that matters.

**1. Sign-in works.** Private window → `https://finn.yourdomain.com`. You
should get a Cloudflare sign-in page, *not* Finn. Enter email, then code.

**2. A stranger is refused.** Someone not on the policy should be stopped at
Cloudflare and never reach Finn.

**3. There is no way around the front door.** ← *the one people skip*

- **Path A**, from another machine on your network:
  ```bash
  curl -s -o /dev/null -w '%{http_code}\n' http://<host-lan-ip>:8000/health
  ```
  Anything but a timeout or connection-refused means Finn is exposed on your
  LAN, bypassing Access. Fix: `HOST=127.0.0.1`.

- **Path B**: confirm the `finn` service has **no domain** under Settings →
  Networking. If a `*.up.railway.app` URL exists, open it in a private
  window — if Finn loads, Access is decorative. Delete the domain.

**4. Redaction is actually enforcing.** A container missing `en_core_web_lg`
starts cleanly and then fails on first redaction; if Presidio is missing
entirely, redaction silently becomes a pass-through and PII goes to the model
while the UI still says it's on. `/health` now reports this:

```bash
curl -s https://finn.yourdomain.com/health | python -m json.tool
```

```json
"redaction": {
  "enabled": true,
  "engine_available": true,
  "model_ready": true,
  "enforcing": true
}
```

**`"enforcing": true` is the one to check, on every deploy.** Anything else
means the boundary is off. (You'll need to be signed in for this to return
JSON rather than a Cloudflare login page.)

---

## 5. Managing testers

**Add / remove:** Zero Trust → Access → Applications → Finn → Configure →
policy → edit the email list. Takes effect on next sign-in.

**Revoke immediately:** Zero Trust → Users → select → **Revoke sessions**.
Removing them from the policy alone leaves an existing session valid.

**Who saw what:** Zero Trust → Logs → Access. Per-user, per-request.

**Sharing between testers:** each tester's collections are their own. Use
Finn's in-app share rather than putting two people on one login — shared
logins destroy the audit trail that makes this defensible.

Send new testers [TESTING.md](TESTING.md); they can skip its §1 setup section.

---

## 6. Troubleshooting

| Symptom | Cause |
|---|---|
| Railway build fails on `spacy download` | Out of build memory. Retry; if persistent, use a Pro builder. |
| Railway **502 "Application failed to respond"** | App not on `$PORT`. The current Dockerfile handles this — check you didn't override `PORT` manually. |
| Tunnel stuck **Unhealthy**, Path B | Wrong internal hostname (must be `SERVICE.railway.internal:8000`), or a pre-Oct-2025 environment needing `HOST=::`. |
| **Error 1016** at the hostname | Tunnel not running. `cloudflared tunnel info finn`. |
| Finn **401**s every request after sign-in | `ACCESS_AUD` / `ACCESS_TEAM_DOMAIN` mismatch. Finn logs the reason at WARNING. |
| **401** although Cloudflare sign-in worked | Request reached the origin without traversing Access. Re-run check 3. |
| Signed in, **no collections** | Ownership migration (§2.5) skipped, or run with a different address than you sign in with. |
| Uploads vanish after deploy, Path B | Volume missing or not mounted at `/app/data`. |
| `"enforcing": false` in `/health` | See check 4. Do not let testers upload real data until it's true. |
| Code email never arrives | Mail scanner ate it. Allowlist `noreply@notify.cloudflare.com`. |

---

## 7. What you are taking on

**Path B moves client data onto Railway's servers.** That's a defensible
choice for a pilot, but it changes what you can claim.
[COMPLIANCE.md](COMPLIANCE.md) and the local-first framing in
[ROADMAP.md](ROADMAP.md) describe an app running on the advisor's own
hardware. Hosted, Railway becomes a subprocessor you'd have to name — which is
exactly the question you're chasing Ollama about in §7 of the roadmap. Two
honest options: keep hosted pilots to anonymized or sample data, or add
Railway to the subprocessor list and get a DPA before real client files land.

Neither path gives you: encryption at rest (Railway encrypts volumes; your own
disk is your job), protection against an authorized tester exporting what they
can see, or SOC 2. Hosting one instance for people you know is a pilot.
Hosting for a firm that isn't you is a different product with a different
compliance surface.

**Backups.** Path A: back up `./data`. Path B: Railway volumes have no
automatic backup on Hobby — periodically `railway run tar czf` the volume down
to your machine, or accept that a pilot's data is disposable and say so.

**A last sequencing note:** get `"enforcing": true` and check 3 passing
*before* you send the first invite. Both are much harder to fix once someone
has uploaded a real client file.
