@echo off
REM ============================================================
REM  Asymptote — Electron desktop build for Windows
REM  Output: electron-dist/Asymptote Setup <version>.exe
REM
REM  Requirements:
REM    - Python 3.11+ with venv
REM    - Node.js 20+
REM    - Git (for version tagging)
REM ============================================================

setlocal enabledelayedexpansion
set "ROOT=%~dp0"
cd /d "%ROOT%"

echo.
echo ============================================================
echo  Asymptote Windows Build
echo ============================================================
echo.

REM ── 1. Build Vue frontend ────────────────────────────────────
echo [1/4] Building Vue frontend (Electron build)...
cd "%ROOT%frontend"
REM Always do a clean install — node_modules and package-lock.json may have been
REM created on Linux/WSL, causing EBADPLATFORM errors for optional deps like esbuild.
echo       Cleaning previous install...
if exist "node_modules\" rmdir /s /q "node_modules"
if exist "package-lock.json" del /f /q "package-lock.json"
REM Verify lockfile is gone before proceeding
if exist "package-lock.json" (
    echo ERROR: Could not delete package-lock.json. Delete it manually and retry.
    exit /b 1
)
call npm install
if errorlevel 1 ( echo ERROR: npm install failed & exit /b 1 )
REM Build into electron/renderer/ with Electron-specific flags:
REM   VITE_ELECTRON=true  → Vue Router uses hash history (file:// compatible)
set VITE_ELECTRON=true
call npm run build:electron
if errorlevel 1 ( echo ERROR: Vue build failed & exit /b 1 )
set VITE_ELECTRON=
cd "%ROOT%"
echo       Done.
echo.

REM ── 2. Set up Python venv and install deps ───────────────────
echo [2/4] Setting up Python environment...
if not exist "%ROOT%venv\" (
    python -m venv "%ROOT%venv"
    if errorlevel 1 ( echo ERROR: Failed to create venv & exit /b 1 )
)
call "%ROOT%venv\Scripts\activate.bat"
pip install -q -r "%ROOT%desktop\requirements_desktop.txt"
if errorlevel 1 ( echo ERROR: pip install failed & exit /b 1 )

REM Ensure spaCy NLP model is present (required by Presidio PII redaction)
python -c "import en_core_web_lg" 2>nul
if errorlevel 1 (
    echo       Downloading spaCy en_core_web_lg model...
    python -m spacy download en_core_web_lg
    if errorlevel 1 (
        echo       Falling back to en_core_web_sm...
        python -m spacy download en_core_web_sm
    )
)
echo       Done.
echo.

REM ── 3. Build Python backend with PyInstaller ─────────────────
echo [3/4] Packaging Python backend with PyInstaller...
if exist "%ROOT%dist\Asymptote\" (
    echo       Removing previous build...
    rmdir /s /q "%ROOT%dist\Asymptote"
)
pyinstaller "%ROOT%desktop\build_desktop.spec" --clean --noconfirm
if errorlevel 1 ( echo ERROR: PyInstaller build failed & exit /b 1 )

REM Safety: strip any runtime user data that crept into the bundle
if exist "%ROOT%dist\Asymptote\data\" (
    rmdir /s /q "%ROOT%dist\Asymptote\data"
)
echo       Done.
echo.

REM ── 4. Build Electron installer ──────────────────────────────
echo [4/4] Building Electron installer...
cd "%ROOT%electron"
call npm install
if errorlevel 1 ( echo ERROR: npm install failed in electron/ & exit /b 1 )
call npm run build:win
if errorlevel 1 ( echo ERROR: electron-builder failed & exit /b 1 )
cd "%ROOT%"
echo       Done.
echo.

echo ============================================================
echo  Build complete!
echo  Installer: electron-dist\Asymptote Setup *.exe
echo ============================================================
echo.
pause
