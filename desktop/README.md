# Asymptote Desktop Application

This directory contains files for building Asymptote as a standalone Windows desktop application.

## Quick Start

To build the desktop application:

```bash
cd desktop
build_windows.bat
```

This creates a standalone executable at `dist/Asymptote/Asymptote.exe`

## Files in This Directory

- **asymptote_desktop.py** - Main desktop application wrapper
- **build_desktop.spec** - PyInstaller configuration
- **requirements_desktop.txt** - Desktop-specific dependencies
- **build_windows.bat** - Automated build script for Windows
- **installer.iss** - Inno Setup script for creating installer
- **build_installer.bat** - Script to build Windows installer
- **DESKTOP_BUILD.md** - Comprehensive build and distribution guide

## Documentation

See [DESKTOP_BUILD.md](DESKTOP_BUILD.md) for complete instructions on:
- Building the application
- Creating installers
- Distribution options
- Troubleshooting
- Advanced configuration

## Requirements

- Python 3.13 (or 3.8+)
- Node.js 20+ (for building frontend)
- Inno Setup 6 (optional, for creating installer)

## Output

After building, you'll have:

- `dist/Asymptote/` - Folder containing the executable and dependencies
- `installer_output/` - Windows installer (if built)

## Notes

- The desktop app runs the FastAPI server on localhost only
- Includes system tray support (optional)
- All dependencies are bundled - no installation required
- User data stored in the `data/` subdirectory
