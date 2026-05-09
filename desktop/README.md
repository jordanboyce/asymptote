# Finn Desktop — Backend Bundle

This directory holds the PyInstaller pieces that package the FastAPI backend
into a standalone executable. The Electron shell in [`electron/`](../electron/)
spawns that executable and provides the window, tray, and installer.

The full build is orchestrated from the repo root:

```bat
:: Windows
build_electron_win.bat
```

```bash
# macOS
./build_electron_mac.sh
```

That script builds the Vue renderer, runs PyInstaller against
`build_desktop.spec` to produce `dist/Finn/Finn.exe`, and then invokes
`electron-builder` to package both into `electron-dist/Finn Setup *.exe`.

## Files

- **finn_desktop.py** — entry script for the bundled exe; starts FastAPI in
  headless mode when launched by Electron (or with a tray icon when run alone).
- **build_desktop.spec** — PyInstaller spec used by step 3 of the build.
- **requirements_desktop.txt** — pip dependencies for the build venv.
- **generate_third_party_licenses.py** — emits `THIRD_PARTY_LICENSES.txt` from
  the active venv before PyInstaller runs.
- **icon.ico** — tray / window icon embedded in the bundled exe.
- **LICENSE** — license text shipped alongside the exe.

## Output

- `dist/Finn/Finn.exe` — PyInstaller bundle of the backend.
- `electron-dist/Finn Setup <version>.exe` — final installer (produced by
  `electron-builder`, not by anything in this directory).
