# Requirements Document

## Introduction

The product is being rebranded from **Asymptote** to **Finn**. Every occurrence of the old name must be replaced across all source files, configuration files, build scripts, infrastructure definitions, and documentation. The rename is purely mechanical — no functional behavior changes. All existing functionality must continue to work after the rename.

## Glossary

- **Finn**: The new product name, replacing "Asymptote" in all user-facing strings.
- **finn**: The lowercase identifier form of the new name, replacing "asymptote" in all internal identifiers, config values, file names, and Docker/package names.
- **Finn_Desktop**: The renamed desktop entry-point module (`finn_desktop.py`), replacing `asymptote_desktop.py`.
- **Rename_Target**: Any file, string, or identifier that contains "Asymptote" or "asymptote" (case-sensitive) outside of `.git/` directories.
- **Non-regression**: The requirement that all existing functionality continues to operate correctly after the rename.

---

## Requirements

### Requirement 1: Python Source Files

**User Story:** As a developer, I want all Python source files to reference "Finn" instead of "Asymptote", so that the codebase is internally consistent with the new brand.

#### Acceptance Criteria

1. THE `main.py` module docstring SHALL read "Finn" in place of "Asymptote".
2. WHEN the application starts, THE `main.py` lifespan logger SHALL emit "Initializing Finn API..." instead of "Initializing Asymptote API...".
3. WHEN the application is ready, THE `main.py` lifespan logger SHALL emit "Finn API ready" instead of "Asymptote API ready".
4. WHEN the application shuts down, THE `main.py` lifespan logger SHALL emit "Shutting down Finn API..." instead of "Shutting down Asymptote API...".
5. THE FastAPI app title in `main.py` SHALL be "Finn API" instead of "Asymptote API".
6. THE HTML fallback response in `main.py` SHALL display "Finn API" instead of "Asymptote API".
7. THE temp directory prefix in `main.py` SHALL be `finn_upload_` instead of `asymptote_upload_`.
8. THE `config.py` module docstring SHALL reference "Finn" instead of "Asymptote".
9. THE `mcp_server_id` default value in `config.py` SHALL be `"finn"` instead of `"asymptote"`.
10. THE `postgres_url` example comment in `config.py` SHALL reference `finn` instead of `asymptote`.
11. THE `desktop/asymptote_desktop.py` file SHALL be renamed to `desktop/finn_desktop.py`.
12. THE module docstring in `desktop/finn_desktop.py` SHALL reference "Finn" instead of "Asymptote".
13. THE `AsymptoteApp` class in `desktop/finn_desktop.py` SHALL be renamed to `FinnApp`.
14. WHEN the desktop app runs in console mode, THE `FinnApp.run_console` method SHALL print "Finn Desktop Application" instead of "Asymptote Desktop Application".
15. WHEN the desktop app runs in console mode, THE `FinnApp.run_console` method SHALL print "Finn is running in the system tray" instead of "Asymptote is running in the system tray".
16. WHEN the desktop app opens the browser, THE `FinnApp.open_browser` method SHALL log "Opening Finn at ..." instead of "Opening Asymptote at ...".
17. THE tray icon label in `desktop/finn_desktop.py` SHALL be "Finn" instead of "Asymptote".
18. THE tray menu item label in `desktop/finn_desktop.py` SHALL read "Open Finn" instead of "Open Asymptote".
19. THE `argparse` description in `desktop/finn_desktop.py` SHALL read "Finn Desktop Application" instead of "Asymptote Desktop Application".
20. THE temp file prefix for the CA bundle in `desktop/finn_desktop.py` SHALL be `finn_ca_` instead of `asymptote_ca_`.

---

### Requirement 2: Electron / Frontend Files

**User Story:** As a user, I want the Electron desktop application to display "Finn" in all window titles, dialog messages, and tray labels, so that the UI reflects the new brand.

#### Acceptance Criteria

1. THE `electron/main.js` window title SHALL be `'Finn'` instead of `'Asymptote'`.
2. THE `electron/main.js` tray tooltip SHALL be `'Finn'` instead of `'Asymptote'`.
3. THE tray context menu item in `electron/main.js` SHALL read `'Open Finn'` instead of `'Open Asymptote'`.
4. WHEN the backend exits unexpectedly, THE error dialog title in `electron/main.js` SHALL be `'Finn'` instead of `'Asymptote'`.
5. WHEN the backend exits unexpectedly, THE error dialog message in `electron/main.js` SHALL read "The Finn backend stopped unexpectedly." instead of "The Asymptote backend stopped unexpectedly."
6. WHEN startup fails, THE error dialog title in `electron/main.js` SHALL be `'Finn failed to start'` instead of `'Asymptote failed to start'`.
7. WHEN startup fails, THE error dialog detail in `electron/main.js` SHALL reference "the Finn backend" instead of "the Asymptote backend".
8. THE backend executable path in `electron/main.js` SHALL reference `Finn.exe` (Windows) and `Finn` (macOS/Linux) instead of `Asymptote.exe` / `Asymptote`.
9. THE dev-mode dist path in `electron/main.js` SHALL reference `dist/Finn` instead of `dist/Asymptote`.
10. THE `contextBridge.exposeInMainWorld` call in `electron/preload.js` SHALL use `'finn'` as the world name instead of `'asymptote'`.
11. THE `electron/package.json` `"name"` field SHALL be `"finn"` instead of `"asymptote"`.
12. THE `electron/package.json` `"build.productName"` field SHALL be `"Finn"` instead of `"Asymptote"`.
13. THE `electron/package.json` `"build.appId"` field SHALL be `"dev.cyberlion.finn"` instead of `"dev.cyberlion.asymptote"`.
14. THE `electron/package.json` `"build.extraResources[0].from"` field SHALL reference `"../dist/Finn"` instead of `"../dist/Asymptote"`.
15. THE `electron/package.json` `"build.nsis.shortcutName"` field SHALL be `"Finn"` instead of `"Asymptote"`.
16. THE `electron/package.json` `"build.dmg.title"` field SHALL read `"Finn ${version}"` instead of `"Asymptote ${version}"`.

---

### Requirement 3: Configuration and Environment Files

**User Story:** As a developer or operator, I want all configuration and environment files to use "finn" identifiers, so that deployments and local setups are consistent with the new brand.

#### Acceptance Criteria

1. THE `.env.example` comment header SHALL reference "Finn" instead of "Asymptote".
2. THE `MCP_SERVER_ID` value in `.env.example` SHALL be `finn` instead of `asymptote`.
3. THE `Dockerfile` comment header SHALL reference "Finn" instead of "Asymptote".
4. THE `Dockerfile.enterprise` `LABEL maintainer` value SHALL reference "Finn" instead of "Asymptote".
5. THE `Dockerfile.enterprise` `LABEL description` value SHALL reference "Finn" instead of "Asymptote".
6. THE `Dockerfile.enterprise` non-root group creation command SHALL use `finn` instead of `asymptote` (i.e., `groupadd -r finn`).
7. THE `Dockerfile.enterprise` non-root user creation command SHALL use `finn` instead of `asymptote` (i.e., `useradd -r -g finn ... finn`).
8. THE `Dockerfile.enterprise` home directory path SHALL be `/home/finn` instead of `/home/asymptote`.
9. THE `Dockerfile.enterprise` `chown` commands SHALL reference `finn:finn` instead of `asymptote:asymptote`.

---

### Requirement 4: Docker Compose Files

**User Story:** As an operator, I want all Docker Compose service names, container names, volume names, and network names to use "finn", so that deployments are identifiable under the new brand.

#### Acceptance Criteria

1. THE `docker-compose.yml` service name SHALL be `finn` instead of `asymptote`.
2. THE `docker-compose.yml` container name SHALL be `finn-api` instead of `asymptote-api`.
3. THE `docker-compose.enterprise.yml` compose `name` field SHALL be `finn-enterprise` instead of `asymptote-enterprise`.
4. THE `docker-compose.enterprise.yml` volume `finn-data` SHALL replace `asymptote-data` in all volume definitions and references.
5. THE `docker-compose.enterprise.yml` volume `finn-models` SHALL replace `asymptote-models` in all volume definitions and references.
6. THE `docker-compose.enterprise.yml` volume `finn-pgdata` SHALL replace `asymptote-pgdata` in all volume definitions and references.
7. THE `docker-compose.enterprise.yml` volume `finn-ollama-data` SHALL replace `asymptote-ollama-data` in all volume definitions and references.
8. THE `docker-compose.enterprise.yml` network `finn-frontend` SHALL replace `asymptote-frontend` in all network definitions and references.
9. THE `docker-compose.enterprise.yml` network `finn-backend` SHALL replace `asymptote-backend` in all network definitions and references.
10. THE `docker-compose.enterprise.yml` container name for the nginx service SHALL be `finn-nginx` instead of `asymptote-nginx`.
11. THE `docker-compose.enterprise.yml` container name for the API service SHALL be `finn-api` instead of `asymptote-api`.
12. THE `docker-compose.enterprise.yml` container name for the postgres service SHALL be `finn-postgres` instead of `asymptote-postgres`.
13. THE `docker-compose.enterprise.yml` container name for the ollama service SHALL be `finn-ollama` instead of `asymptote-ollama`.
14. THE `docker-compose.enterprise.yml` API image name SHALL be `finn-enterprise:latest` instead of `asymptote-enterprise:latest`.
15. THE `docker-compose.enterprise.yml` model cache volume mount path SHALL be `/home/finn/.cache` instead of `/home/asymptote/.cache`.
16. THE `docker-compose.enterprise.yml` `POSTGRES_URL` default value SHALL reference `finn` credentials instead of `asymptote` (i.e., `postgresql://finn:finn@postgres:5432/finn`).
17. THE `docker-compose.enterprise.yml` PostgreSQL `POSTGRES_DB` default SHALL be `finn` instead of `asymptote`.
18. THE `docker-compose.enterprise.yml` PostgreSQL `POSTGRES_USER` default SHALL be `finn` instead of `asymptote`.
19. THE `docker-compose.enterprise.yml` PostgreSQL `POSTGRES_PASSWORD` default SHALL be `finn` instead of `asymptote`.
20. THE `docker-compose.enterprise.yml` PostgreSQL health check command SHALL reference `-U finn` instead of `-U asymptote`.
21. THE `docker-compose.enterprise.yml` comment header SHALL reference "Finn" instead of "Asymptote".

---

### Requirement 5: Build and Installer Scripts

**User Story:** As a developer, I want all build scripts and installer definitions to reference "Finn" so that build artifacts are named correctly.

#### Acceptance Criteria

1. THE `build_electron_mac.sh` script header comment SHALL reference "Finn" instead of "Asymptote".
2. THE `build_electron_mac.sh` echo output SHALL display "Finn macOS Build" instead of "Asymptote macOS Build".
3. THE `build_electron_mac.sh` PyInstaller cleanup path SHALL reference `dist/Finn` instead of `dist/Asymptote`.
4. THE `build_electron_mac.sh` final output message SHALL reference `electron-dist/Finn-*.dmg` instead of `electron-dist/Asymptote-*.dmg`.
5. THE `build_electron_win.bat` script header comment SHALL reference "Finn" instead of "Asymptote".
6. THE `build_electron_win.bat` echo output SHALL display "Finn Windows Build" instead of "Asymptote Windows Build".
7. THE `build_electron_win.bat` PyInstaller cleanup path SHALL reference `dist\Finn` instead of `dist\Asymptote`.
8. THE `build_electron_win.bat` final output message SHALL reference `electron-dist\Finn Setup *.exe` instead of `electron-dist\Asymptote Setup *.exe`.
9. THE `desktop/build_desktop.spec` comment header SHALL reference "Finn" instead of "Asymptote".
10. THE `desktop/build_desktop.spec` `Analysis` entry point SHALL reference `finn_desktop.py` instead of `asymptote_desktop.py`.
11. THE `desktop/build_desktop.spec` `EXE` `name` parameter SHALL be `'Finn'` instead of `'Asymptote'`.
12. THE `desktop/build_desktop.spec` `COLLECT` `name` parameter SHALL be `'Finn'` instead of `'Asymptote'`.
13. THE `desktop/installer.iss` `MyAppName` define SHALL be `"Finn"` instead of `"Asymptote"`.
14. THE `desktop/installer.iss` `MyAppExeName` define SHALL be `"Finn.exe"` instead of `"Asymptote.exe"`.
15. THE `desktop/installer.iss` `MyAppURL` define SHALL reference the finn GitHub URL instead of the asymptote URL.
16. THE `desktop/installer.iss` `OutputBaseFilename` SHALL be `Finn-Setup-{#MyAppVersion}` instead of `Asymptote-Setup-{#MyAppVersion}`.
17. THE `desktop/installer.iss` `[Files]` source paths SHALL reference `dist\Finn\` instead of `dist\Asymptote\`.

---

### Requirement 6: Infrastructure Files

**User Story:** As an operator, I want nginx and certificate configuration to use "finn" identifiers, so that infrastructure components are consistent with the new brand.

#### Acceptance Criteria

1. THE `nginx/nginx.conf` comment header SHALL reference "Finn" instead of "Asymptote".
2. THE `nginx/nginx.conf` upstream block name SHALL be `finn_backend` instead of `asymptote_backend`.
3. ALL `proxy_pass` directives in `nginx/nginx.conf` SHALL reference `http://finn_backend` instead of `http://asymptote_backend`.
4. THE `nginx/generate-self-signed-cert.sh` OpenSSL config `O` field SHALL be `Finn` instead of `Asymptote`.
5. THE `nginx/generate-self-signed-cert.sh` `subjectAltName` SHALL include `DNS:finn` instead of `DNS:asymptote`.

---

### Requirement 7: Documentation Files

**User Story:** As a reader, I want all documentation to refer to the product as "Finn", so that the documentation is accurate and consistent.

#### Acceptance Criteria

1. THE `ROADMAP.md` title and all body references SHALL use "Finn" instead of "Asymptote".
2. THE `ADVISOR_USE_CASE.md` title and all body references SHALL use "Finn" instead of "Asymptote".
3. THE `desktop/DESKTOP_BUILD.md` SHALL reference `finn_desktop.py` instead of `asymptote_desktop.py` in all instructions and code examples.
4. THE `.impeccable.md` design principles document SHALL reference "Finn" instead of "Asymptote".
5. THE `.github/copilot-instructions.md` development guidelines SHALL reference "Finn" instead of "Asymptote".

---

### Requirement 8: Non-Regression

**User Story:** As a user, I want the application to function identically after the rename, so that the rebrand does not introduce any regressions.

#### Acceptance Criteria

1. WHEN the Electron app is launched after the rename, THE application SHALL start the backend and load the UI without errors.
2. WHEN the Python backend starts after the rename, THE FastAPI server SHALL initialize all services and respond to `/health` with status `"healthy"`.
3. WHEN the MCP server is enabled after the rename, THE MCP server SHALL register under the identifier `finn` and respond to MCP client connections.
4. WHEN the `contextBridge` is accessed in the renderer after the rename, THE renderer SHALL access the API URL via `window.finn.apiUrl` instead of `window.asymptote.apiUrl`.
5. WHEN Docker Compose is started after the rename, THE `finn` service SHALL start, pass its health check, and be reachable via the nginx proxy.
6. WHEN the desktop installer is built after the rename, THE output executable SHALL be named `Finn.exe` and install to the `Finn` program directory.
7. WHEN the macOS DMG is built after the rename, THE output file SHALL be named `Finn-<version>.dmg`.

---

### Requirement 9: Completeness Verification

**User Story:** As a developer, I want to verify that no occurrences of the old name remain after the rename, so that the rebrand is complete and no stale references are left.

#### Acceptance Criteria

1. WHEN a case-insensitive search for `asymptote` is run across all non-git, non-binary files in the repository, THE search SHALL return zero matches.
2. WHEN a case-sensitive search for `Asymptote` is run across all non-git, non-binary files in the repository, THE search SHALL return zero matches.
3. WHEN a case-sensitive search for `asymptote` is run across all non-git, non-binary files in the repository, THE search SHALL return zero matches.
4. THE file `desktop/asymptote_desktop.py` SHALL NOT exist after the rename.
5. THE file `desktop/finn_desktop.py` SHALL exist after the rename.
