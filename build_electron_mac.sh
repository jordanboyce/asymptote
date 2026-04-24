#!/usr/bin/env bash
# ============================================================
#  Asymptote — Electron desktop build for macOS
#  Output: electron-dist/Asymptote-<version>.dmg
#
#  Requirements:
#    - Python 3.11+ (recommend pyenv or Homebrew)
#    - Node.js 20+ (recommend nvm or Homebrew)
#    - Xcode Command Line Tools: xcode-select --install
#
#  Code signing (optional but recommended for distribution):
#    Set APPLE_ID, APPLE_ID_PASSWORD, APPLE_TEAM_ID env vars
#    and add your Developer ID cert to Keychain before running.
# ============================================================

set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"

echo ""
echo "============================================================"
echo " Asymptote macOS Build"
echo "============================================================"
echo ""

# ── 1. Build Vue frontend (Electron build) ────────────────────
echo "[1/4] Building Vue frontend (Electron build)..."
cd "$ROOT/frontend"
npm install
# Build into electron/renderer/ with Electron-specific flags:
#   VITE_ELECTRON=true  → Vue Router uses hash history (file:// compatible)
VITE_ELECTRON=true npm run build:electron
cd "$ROOT"
echo "      Done."
echo ""

# ── 2. Set up Python venv and install deps ────────────────────
echo "[2/4] Setting up Python environment..."
if [ ! -d "$ROOT/venv" ]; then
    python3 -m venv "$ROOT/venv"
fi
source "$ROOT/venv/bin/activate"
pip install -q -r "$ROOT/desktop/requirements_desktop.txt"

# Ensure spaCy NLP model is present (required by Presidio PII redaction)
if ! python -c "import en_core_web_lg" 2>/dev/null; then
    echo "      Downloading spaCy en_core_web_lg model..."
    python -m spacy download en_core_web_lg || {
        echo "      Falling back to en_core_web_sm..."
        python -m spacy download en_core_web_sm
    }
fi
echo "      Done."
echo ""

# ── 3. Build Python backend with PyInstaller ──────────────────
echo "[3/4] Packaging Python backend with PyInstaller..."
rm -rf "$ROOT/dist/Asymptote"
pyinstaller "$ROOT/desktop/build_desktop.spec" --clean --noconfirm

# Safety: strip any runtime user data that crept into the bundle
rm -rf "$ROOT/dist/Asymptote/data"
echo "      Done."
echo ""

# ── 4. Build Electron .dmg ────────────────────────────────────
echo "[4/4] Building Electron .dmg..."
cd "$ROOT/electron"
npm install --silent
npm run build:mac
cd "$ROOT"
echo "      Done."
echo ""

echo "============================================================"
echo " Build complete!"
echo " Installer: electron-dist/Asymptote-*.dmg"
echo "============================================================"
echo ""
