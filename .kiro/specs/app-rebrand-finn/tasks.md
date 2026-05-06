# Implementation Plan: App Rebrand — Asymptote → Finn

## Overview

Mechanical rename of every occurrence of "Asymptote" / "asymptote" across the codebase.
No functional changes. Scope is limited to what matters for the pilot:
Python backend, config, Docker/nginx infrastructure, and documentation.

Electron, PyInstaller, and installer scripts are **out of scope** — tabled for later.
The desktop launcher (`desktop/asymptote_desktop.py`) is renamed and updated since it
is the pilot's launch path, but no build tooling changes are included.

---

## Tasks

- [x] 1. Python source files
  - [x] 1.1 Edit `main.py` — replace all Asymptote references
    - Change module docstring: `"Asymptote — Self-hosted document search API."` → `"Finn — Self-hosted document search API."`
    - Change lifespan logger: `"Initializing Asymptote API..."` → `"Initializing Finn API..."`
    - Change lifespan logger: `"Asymptote API ready"` → `"Finn API ready"`
    - Change lifespan logger: `"Shutting down Asymptote API..."` → `"Shutting down Finn API..."`
    - Change FastAPI app title: `"Asymptote API"` → `"Finn API"`
    - Change FastAPI app description to reference Finn
    - Change HTML fallback response: `"<h1>Asymptote API</h1>"` → `"<h1>Finn API</h1>"`
    - Change temp directory prefix: `asymptote_upload_` → `finn_upload_`

  - [x] 1.2 Edit `config.py` — replace all Asymptote references
    - Change module docstring: `"Configuration management for Asymptote API."` → `"Configuration management for Finn API."`
    - Change `mcp_server_id` default: `"asymptote"` → `"finn"`
    - Change `postgres_url` example comment: `asymptote` → `finn`

  - [x] 1.3 Rename `desktop/asymptote_desktop.py` → `desktop/finn_desktop.py`
    - Run `git mv desktop/asymptote_desktop.py desktop/finn_desktop.py` to preserve git history

  - [x] 1.4 Edit `desktop/finn_desktop.py` — replace all Asymptote references
    - Change module docstring: `"Asymptote Desktop Application"` → `"Finn Desktop Application"`
    - Change temp CA bundle prefix: `asymptote_ca_` → `finn_ca_`
    - Rename class `AsymptoteApp` → `FinnApp` (class definition + all instantiation sites in the file)
    - Change `open_browser` log message: `"Opening Asymptote at"` → `"Opening Finn at"`
    - Change `run_console` print: `"Asymptote Desktop Application"` → `"Finn Desktop Application"`
    - Change `run_console` print: `"Asymptote is running in the system tray"` → `"Finn is running in the system tray"`
    - Change tray `MenuItem`: `"Open Asymptote"` → `"Open Finn"`
    - Change tray `Icon` label: `"Asymptote"` → `"Finn"`
    - Change tray tooltip: `"Asymptote Search"` → `"Finn"`
    - Change `argparse` description: `"Asymptote Desktop Application"` → `"Finn Desktop Application"`

- [x] 2. Configuration and environment files
  - [x] 2.1 Edit `.env.example` — replace all Asymptote references
    - Change comment header: `# Asymptote API Configuration` → `# Finn API Configuration`
    - Change `MCP_SERVER_ID` value: `asymptote` → `finn`

  - [x] 2.2 Edit `Dockerfile` — replace all Asymptote references
    - Change comment header: `# Asymptote API Dockerfile` → `# Finn API Dockerfile`

  - [x] 2.3 Edit `Dockerfile.enterprise` — replace all Asymptote references
    - Change any `LABEL` values referencing Asymptote → Finn
    - Change `groupadd -r asymptote` → `groupadd -r finn`
    - Change `useradd -r -g asymptote ... asymptote` → `useradd -r -g finn ... finn`
    - Change home directory path: `/home/asymptote` → `/home/finn` (mkdir and chown lines)
    - Change `chown asymptote:asymptote` → `chown finn:finn`

  - [x] 2.4 Edit `Dockerfile.corporate` — replace all Asymptote references
    - Apply same changes as 2.2 (comment header, any label or user references)

- [x] 3. Docker Compose files
  - [x] 3.1 Edit `docker-compose.yml` — replace all Asymptote references
    - Change service name: `asymptote:` → `finn:`
    - Change `container_name`: `asymptote-api` → `finn-api`

  - [x] 3.2 Edit `docker-compose.enterprise.yml` — replace all Asymptote references
    - Change compose `name`: `asymptote-enterprise` → `finn-enterprise`
    - Change comment header to reference Finn
    - Rename all volumes in the top-level `volumes:` block and every service reference:
      - `asymptote-data` → `finn-data`
      - `asymptote-models` → `finn-models`
      - `asymptote-pgdata` → `finn-pgdata`
      - `asymptote-ollama-data` → `finn-ollama-data`
    - Rename all networks in the top-level `networks:` block and every service reference:
      - `asymptote-frontend` → `finn-frontend`
      - `asymptote-backend` → `finn-backend`
    - Change container names: `asymptote-nginx` → `finn-nginx`, `asymptote-api` → `finn-api`, `asymptote-postgres` → `finn-postgres`, `asymptote-ollama` → `finn-ollama`
    - Change API service name: `asymptote-api:` → `finn-api:`
    - Change API image: `asymptote-enterprise:latest` → `finn-enterprise:latest`
    - Change model cache mount path: `/home/asymptote/.cache` → `/home/finn/.cache`
    - Change `POSTGRES_URL` default: `postgresql://asymptote:asymptote@postgres:5432/asymptote` → `postgresql://finn:finn@postgres:5432/finn`
    - Change `POSTGRES_DB` default: `asymptote` → `finn`
    - Change `POSTGRES_USER` default: `asymptote` → `finn`
    - Change `POSTGRES_PASSWORD` default: `asymptote` → `finn`
    - Change postgres health check: `pg_isready -U asymptote` → `pg_isready -U finn`

- [x] 4. Infrastructure files
  - [x] 4.1 Edit `nginx/nginx.conf` — replace all Asymptote references
    - Change any comment header referencing Asymptote → Finn
    - Change upstream block name: `upstream asymptote_backend {` → `upstream finn_backend {`
    - Change upstream server reference: `server asymptote-api:8000` → `server finn-api:8000`
    - Change all `proxy_pass` directives: `http://asymptote_backend` → `http://finn_backend` (multiple location blocks — update all)

  - [x] 4.2 Edit `nginx/generate-self-signed-cert.sh` — replace all Asymptote references
    - Change `O = Asymptote` → `O = Finn` in the embedded OpenSSL config
    - Change `subjectAltName`: `DNS:asymptote` → `DNS:finn`

- [x] 5. Documentation files
  - [x] 5.1 Edit `ROADMAP.md` — replace all Asymptote references
    - Global find-and-replace: `Asymptote` → `Finn` throughout (title, body, tables, section headers)

  - [x] 5.2 Edit `ADVISOR_USE_CASE.md` — replace all Asymptote references
    - Global find-and-replace: `Asymptote` → `Finn` throughout

  - [x] 5.3 Edit `.impeccable.md` — replace all Asymptote references
    - Global find-and-replace: `Asymptote` → `Finn` throughout

  - [x] 5.4 Edit `.github/copilot-instructions.md` (if it exists) — replace all Asymptote references
    - Global find-and-replace: `Asymptote` → `Finn` throughout

  - [x] 5.5 Edit `CONTEXT.md` — replace all Asymptote references
    - Global find-and-replace: `Asymptote` → `Finn` throughout

- [x] 6. Verification
  - [x] 6.1 Confirm the desktop launcher rename is complete
    - Verify `desktop/asymptote_desktop.py` no longer exists
    - Verify `desktop/finn_desktop.py` exists and the app starts cleanly with `python desktop/finn_desktop.py`

  - [x] 6.2 Run grep to confirm zero remaining occurrences in pilot-scope files
    - Run: `grep -rn -i "asymptote" . --include="*.py" --include="*.yml" --include="*.yaml" --include="*.md" --include="*.conf" --include="*.sh" --include="*.env*" --include="Dockerfile*" --include="*.json" --exclude-dir=.git --exclude-dir=node_modules --exclude-dir=.pytest_cache`
    - The command SHALL return zero matches
    - Note: Electron/PyInstaller files (`electron/`, `build_electron_*.sh`, `build_electron_*.bat`, `desktop/build_desktop.spec`, `desktop/installer.iss`) are excluded from this check — they are tabled for the installer milestone

## Notes

- Task 1.3 (file rename) must be completed before task 1.4 (content edits).
- `docker-compose.enterprise.yml` has the highest change density — update both the top-level definition blocks (`volumes:`, `networks:`) and every per-service reference.
- `nginx/nginx.conf` has multiple `proxy_pass` directives across different location blocks — update all of them.
- `ROADMAP.md` is ~700 lines; a global find-and-replace is the right approach.
- Binary files (`.ico`, `.db`, `.npy`, `.index`) contain no text references and are excluded from verification.
- Electron and build script files (`electron/`, `build_electron_*.sh`, `build_electron_*.bat`, `desktop/build_desktop.spec`, `desktop/installer.iss`) are intentionally left unchanged — they will be addressed in the desktop installer milestone.
