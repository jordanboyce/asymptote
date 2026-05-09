"""
Finn Desktop Application
Runs the FastAPI server and opens the UI in the default browser.
Can run in system tray mode.
"""

import sys
import os
import webbrowser
import threading
import time
import socket
from pathlib import Path


def _resolve_user_data_dir() -> Path:
    """Pick a stable per-user data directory for the bundled desktop app.

    Order of precedence:
      1. ``FINN_DATA_DIR`` (set by the Electron shell to ``app.getPath('userData')/data``)
      2. ``DATA_DIR`` (already-mapped pydantic field)
      3. Platform default — APPDATA on Windows, ~/Library/Application Support on macOS,
         ~/.local/share elsewhere.

    Critical: ``data_dir`` defaults to ``./data`` which is relative to cwd.
    Inside a PyInstaller bundle the cwd is unstable (and previously was
    ``sys._MEIPASS``, the per-launch extraction folder, which silently wiped
    user data on every restart). We resolve to an absolute path before
    importing ``config`` so all downstream code sees the same location.
    """
    explicit = os.environ.get("FINN_DATA_DIR") or os.environ.get("DATA_DIR")
    if explicit:
        return Path(explicit).expanduser().resolve()

    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "Finn" / "data"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Finn" / "data"
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "Finn" / "data"


# Add project root to Python path to find main module
if getattr(sys, 'frozen', False):
    # Running as compiled executable
    application_path = sys._MEIPASS

    # Pin DATA_DIR to a stable per-user location *before* config is imported.
    # pydantic-settings reads DATA_DIR env into Settings.data_dir.
    _data_dir = _resolve_user_data_dir()
    _data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["DATA_DIR"] = str(_data_dir)

    # Build a combined CA bundle so HuggingFace downloads work in corporate environments.
    # Merges certifi's default bundle with any .crt/.pem/.cer files from the bundled certs/ dir.
    import tempfile
    import atexit

    _cert_pieces = []

    _certifi_ca = os.path.join(application_path, 'certifi', 'cacert.pem')
    if os.path.exists(_certifi_ca):
        with open(_certifi_ca, 'rb') as _f:
            _cert_pieces.append(_f.read())

    _bundled_certs_dir = os.path.join(application_path, 'certs')
    if os.path.isdir(_bundled_certs_dir):
        for _fname in sorted(os.listdir(_bundled_certs_dir)):
            if _fname.lower().endswith(('.crt', '.pem', '.cer')):
                try:
                    with open(os.path.join(_bundled_certs_dir, _fname), 'rb') as _f:
                        _cert_pieces.append(_f.read())
                except Exception:
                    pass

    if _cert_pieces:
        _fd, _ca_bundle_path = tempfile.mkstemp(suffix='.pem', prefix='finn_ca_')
        with os.fdopen(_fd, 'wb') as _f:
            for _piece in _cert_pieces:
                _f.write(_piece)
                if not _piece.endswith(b'\n'):
                    _f.write(b'\n')

        def _remove_ca_bundle(_path):
            try:
                os.unlink(_path)
            except Exception:
                pass

        atexit.register(_remove_ca_bundle, _ca_bundle_path)

        os.environ['SSL_CERT_FILE'] = _ca_bundle_path
        os.environ['REQUESTS_CA_BUNDLE'] = _ca_bundle_path
        os.environ['CURL_CA_BUNDLE'] = _ca_bundle_path
else:
    # Running as script
    application_path = Path(__file__).parent.parent

sys.path.insert(0, str(application_path))

import uvicorn
from config import settings

# Optional: System tray support (requires pystray)
try:
    from pystray import Icon, Menu, MenuItem
    from PIL import Image, ImageDraw
    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False
    print("Note: Install pystray for system tray support: pip install pystray pillow")


def find_free_port(start_port=8000, max_tries=10):
    """Find a free port starting from start_port."""
    for port in range(start_port, start_port + max_tries):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('127.0.0.1', port))
                return port
        except OSError:
            continue
    raise RuntimeError(f"Could not find a free port in range {start_port}-{start_port + max_tries}")


def create_tray_icon():
    """Load the tray icon from file, or create a simple fallback."""
    # Try to load icon.ico from the application directory
    if getattr(sys, 'frozen', False):
        # Running as compiled executable
        icon_path = Path(sys._MEIPASS) / 'desktop' / 'icon.ico'
        if not icon_path.exists():
            icon_path = Path(sys._MEIPASS) / 'icon.ico'
    else:
        # Running as script
        icon_path = Path(__file__).parent / 'icon.ico'

    try:
        if icon_path.exists():
            # Load the actual icon file
            image = Image.open(icon_path)
            # Convert to RGB if needed (ICO might be RGBA)
            if image.mode == 'RGBA':
                # Create white background
                background = Image.new('RGB', image.size, (255, 255, 255))
                background.paste(image, mask=image.split()[3])  # Use alpha channel as mask
                return background
            return image.convert('RGB')
    except Exception as e:
        print(f"Warning: Could not load icon from {icon_path}: {e}")

    # Fallback: create a simple programmatic icon
    width = 64
    height = 64
    color1 = (75, 85, 255)  # Primary color
    color2 = (255, 255, 255)

    image = Image.new('RGB', (width, height), color1)
    dc = ImageDraw.Draw(image)

    # Draw a simple "A" shape
    dc.polygon([(32, 10), (10, 54), (20, 54), (32, 30), (44, 54), (54, 54)], fill=color2)
    dc.rectangle([26, 38, 38, 54], fill=color1)

    return image


class FinnApp:
    """Desktop application wrapper for Finn."""

    def __init__(self):
        self.server_thread = None
        self.server = None
        self.port = find_free_port(settings.port)
        self.base_url = f"http://localhost:{self.port}"
        self.running = False
        # Stamp data/latest_known.json so /api/version has something to compare
        # the running build against. The default writer records the same version
        # the launcher is shipping — so the banner stays quiet by default.
        # A future auto-update mechanism (out of pilot scope) can overwrite this
        # file post-install with the version the user *should* be running, and
        # the next /api/version call will pick it up automatically.
        self._write_latest_known_marker()

    def _write_latest_known_marker(self):
        """Write data/latest_known.json with the launcher's known version.

        Best-effort — a write failure must not block startup.
        """
        import json
        from datetime import datetime
        try:
            data_dir = Path(settings.data_dir)
            data_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": getattr(settings, "app_version", "0.0.0"),
                "checked_at": datetime.utcnow().isoformat(),
            }
            with open(data_dir / "latest_known.json", "w", encoding="utf-8") as f:
                json.dump(payload, f)
        except Exception as e:
            print(f"Warning: could not write latest_known.json: {e}")

    def start_server(self):
        """Start the FastAPI server in a background thread."""
        # Note: do NOT chdir to sys._MEIPASS here. The PyInstaller extraction
        # dir is wiped on relaunch, and Settings.data_dir resolves relative to
        # cwd by default — chdir-ing into _MEIPASS used to silently destroy
        # user data on every launch. ``main`` is importable via sys.path
        # (set above), and DATA_DIR has already been pinned to an absolute
        # path before config was imported.

        config = uvicorn.Config(
            "main:app",
            host="127.0.0.1",  # Only listen on localhost for desktop app
            port=self.port,
            log_level="info",
            access_log=False,
        )
        self.server = uvicorn.Server(config)
        self.running = True
        self.server.run()

    def open_browser(self):
        """Open the application in the default browser."""
        # Wait for server to start
        max_retries = 30
        for i in range(max_retries):
            try:
                import requests
                response = requests.get(f"{self.base_url}/health", timeout=1)
                if response.status_code == 200:
                    break
            except:
                time.sleep(0.5)
        else:
            print("Warning: Server did not start in time")
            return

        print(f"Opening Finn at {self.base_url}")
        webbrowser.open(self.base_url)

    def run(self, use_tray=True):
        """Run the application."""
        # Start server in background thread
        self.server_thread = threading.Thread(target=self.start_server, daemon=True)
        self.server_thread.start()

        # Wait a moment for server to initialize
        time.sleep(1)

        # Open browser
        self.open_browser()

        if use_tray and HAS_TRAY:
            self.run_with_tray()
        else:
            self.run_console()

    def run_console(self):
        """Run without system tray (console mode)."""
        print(f"\n{'='*60}")
        print("Finn Desktop Application")
        print(f"{'='*60}")
        print(f"\nServer running at: {self.base_url}")
        print("\nPress Ctrl+C to quit")
        print(f"{'='*60}\n")

        try:
            # Keep the main thread alive
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n\nShutting down...")
            self.running = False
            if self.server:
                self.server.should_exit = True

    def run_with_tray(self):
        """Run with system tray icon."""
        icon_image = create_tray_icon()

        def on_open(icon, item):
            webbrowser.open(self.base_url)

        def on_quit(icon, item):
            print("\nShutting down...")
            self.running = False
            if self.server:
                self.server.should_exit = True
            icon.stop()

        menu = Menu(
            MenuItem("Open Finn", on_open, default=True),
            MenuItem("Quit", on_quit)
        )

        icon = Icon("Finn", icon_image, "Finn", menu)

        print(f"\n{'='*60}")
        print("Finn is running in the system tray")
        print(f"Server: {self.base_url}")
        print("Right-click the tray icon to open or quit")
        print(f"{'='*60}\n")

        icon.run()


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Finn Desktop Application")
    parser.add_argument(
        "--no-tray",
        action="store_true",
        help="Run without system tray (console mode)"
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Don't automatically open browser"
    )

    args = parser.parse_args()

    app = FinnApp()

    if args.no_browser:
        # Don't auto-open browser, just start server
        app.start_server()
    else:
        app.run(use_tray=not args.no_tray)


if __name__ == "__main__":
    main()
