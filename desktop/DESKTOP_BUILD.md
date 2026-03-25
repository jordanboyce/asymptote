# Building Asymptote Desktop for Windows

This guide explains how to build Asymptote as a standalone Windows desktop application.

## Overview

The desktop version packages the FastAPI backend, Vue frontend, and all dependencies into a single executable that runs on Windows without requiring Python or Node.js to be installed.

## Features

- **Single Executable**: All dependencies bundled
- **System Tray**: Runs in background with tray icon
- **Auto-Start**: Opens browser automatically
- **Local Only**: Binds to localhost for security
- **Portable**: No installation required (if using folder build)
- **Installer**: Optional professional installer for easy distribution

## Prerequisites

### Required

1. **Python 3.13** (or 3.8+)
2. **Node.js** 20+ (for building frontend)
3. **Git** (for cloning repository)

### Optional (for installer)

4. **Inno Setup 6**: https://jrsoftware.org/isdl.php

## Quick Start

### 1. Build the Executable

Run the build script:

```bash
build_windows.bat
```

This will:
1. Create a virtual environment
2. Install Python dependencies
3. Build the Vue frontend
4. Package everything with PyInstaller

**Output**: `dist/Asymptote/Asymptote.exe`

### 2. Test the Application

```bash
cd dist\Asymptote
Asymptote.exe
```

The application will:
- Start the server on `http://localhost:8000` (or next available port)
- Open your default browser
- Run in the system tray

### 3. Create Installer (Optional)

To create a professional Windows installer:

```bash
build_installer.bat
```

**Output**: `installer_output/Asymptote-Setup-1.0.0.exe`

## Manual Build Steps

If you prefer to build manually:

### 1. Install Dependencies

```bash
# Create virtual environment
python -m venv venv
venv\Scripts\activate

# Install requirements
pip install -r requirements_desktop.txt
```

### 2. Build Frontend

```bash
cd frontend
npm install
npm run build
cd ..
```

### 3. Run PyInstaller

```bash
pyinstaller build_desktop.spec --clean
```

## Configuration

### Desktop Application Options

The `asymptote_desktop.py` script supports command-line options:

```bash
# Run without system tray (console mode)
Asymptote.exe --no-tray

# Run without auto-opening browser
Asymptote.exe --no-browser
```

### Customizing the Build

Edit `build_desktop.spec` to customize:

- **Application name**: Change `name='Asymptote'`
- **Icon**: Set `icon='your_icon.ico'`
- **Console visibility**: Set `console=False` to hide console window
- **Additional files**: Add to `datas` list

### Creating an Icon

To add a custom icon:

1. Create or download an `.ico` file
2. Save as `icon.ico` in the project root
3. Rebuild with `build_windows.bat`

You can create an icon from an image using online tools or ImageMagick:

```bash
# Using ImageMagick
magick convert icon.png -define icon:auto-resize=256,128,64,48,32,16 icon.ico
```

## Distribution

### Folder Distribution

The simplest method - share the entire `dist/Asymptote/` folder:

**Pros**:
- No installation required
- Portable - can run from USB drive
- Easy to update (replace folder)

**Cons**:
- Larger size (includes all dependencies)
- Multiple files to manage

### Installer Distribution

Use the Inno Setup installer for professional distribution:

**Pros**:
- Single `.exe` file
- Professional install/uninstall
- Start menu shortcuts
- Desktop icon option
- Uninstaller in Control Panel

**Cons**:
- Requires Inno Setup to build
- Larger initial download

### Single-File Executable (Advanced)

To create a single `.exe` file, modify `build_desktop.spec`:

```python
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,  # Add this
    a.zipfiles,  # Add this
    a.datas,     # Add this
    [],
    name='Asymptote',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # Hide console
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icon.ico',
)
```

**Warning**: Single-file builds are slower to start and may trigger antivirus warnings.

## Troubleshooting

### Build Fails

**Missing hiddenimports**: If the built app crashes on startup, you may need to add missing imports to `build_desktop.spec`:

```python
hiddenimports=[
    'your.missing.module',
    ...
]
```

**DLL errors**: Ensure all required system DLLs are included or installed on target systems.

### Runtime Issues

**Port already in use**: The app will automatically find the next available port starting from 8000.

**Antivirus blocking**: Some antivirus software flags PyInstaller executables as suspicious. You may need to:
- Add an exception for the executable
- Code-sign the executable (requires certificate)

**Missing data files**: Ensure all data files are listed in the `datas` section of the spec file.

### System Tray Not Working

If `pystray` is not installed:

```bash
pip install pystray pillow
```

Then rebuild. Without pystray, the app runs in console mode.

## Advanced Configuration

### Custom Port

Edit `asymptote_desktop.py`:

```python
self.port = find_free_port(8080)  # Start from port 8080
```

### Auto-Start with Windows

To make Asymptote start automatically:

1. **Via Installer**: Add to Inno Setup script:

```iss
[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Asymptote"; ValueData: """{app}\{#MyAppExeName}"""; Flags: uninsdeletevalue
```

2. **Manual**: Create shortcut in:
```
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
```

### Environment Variables

Desktop mode binds to `127.0.0.1` only. To allow network access, edit `asymptote_desktop.py`:

```python
config = uvicorn.Config(
    "main:app",
    host="0.0.0.0",  # Listen on all interfaces
    port=self.port,
    ...
)
```

## File Structure

After building, the dist folder contains:

```
dist/Asymptote/
├── Asymptote.exe          # Main executable
├── _internal/             # Dependencies
│   ├── Python DLLs
│   ├── Libraries
│   └── Torch models
├── static/                # Frontend files
│   ├── index.html
│   └── assets/
└── data/                  # User data (created at runtime)
    ├── documents/
    └── indexes/
```

## Performance

### Build Size

Typical build sizes:
- **Folder build**: ~1.5 GB (includes PyTorch, transformers)
- **Single-file**: ~1.5 GB (slightly larger due to compression)
- **Installer**: ~500 MB (compressed)

### Optimization

To reduce size:

1. **Remove unused dependencies** from `requirements.txt`
2. **Use smaller embedding models** (change in `config.py`)
3. **Exclude unnecessary files** in `build_desktop.spec`:

```python
excludes=[
    'tkinter',
    'matplotlib',
    'test',
    'tests',
]
```

4. **Use UPX compression** (already enabled in spec file)

### Startup Time

- **First run**: 10-15 seconds (loads ML models)
- **Subsequent runs**: 5-8 seconds
- **Single-file build**: Add 5-10 seconds (extracts files to temp)

## Security

### Desktop vs Server Mode

**Desktop Mode** (asymptote_desktop.py):
- Binds to `127.0.0.1` only
- No CORS restrictions needed
- Safer for untrusted networks

**Server Mode** (main.py):
- Binds to `0.0.0.0` by default
- Allows network access
- Requires CORS configuration

### Code Signing (Optional)

For professional distribution, consider code signing:

1. Obtain code signing certificate
2. Sign the executable:

```bash
signtool sign /f certificate.pfx /p password /t http://timestamp.digicert.com Asymptote.exe
```

Benefits:
- Reduces antivirus false positives
- Shows publisher name in UAC prompts
- Required for some enterprise deployments

## Updates

### Manual Updates

Users replace the executable or reinstall using new installer.

### Auto-Update (Future Enhancement)

Consider implementing auto-update using:
- PyUpdater
- Squirrel.Windows
- Custom update checker

## Support

For issues or questions:
1. Check GitHub issues
2. Review PyInstaller documentation
3. Check Inno Setup documentation (for installer issues)

## License

Ensure your distribution complies with all dependency licenses, especially:
- PyTorch
- Transformers
- FAISS
- Vue.js
