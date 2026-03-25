@echo off
REM Build script for Asymptote Desktop on Windows

echo ========================================
echo Asymptote Desktop Build Script
echo ========================================
echo.

REM Go to parent directory (project root)
cd ..

REM Check if virtual environment exists
if not exist "venv\" (
    echo Creating virtual environment...
    python -m venv venv
)

REM Activate virtual environment
echo Activating virtual environment...
call venv\Scripts\activate.bat

REM Install dependencies
echo.
echo Installing dependencies...
pip install -r desktop\requirements_desktop.txt

REM Build frontend
echo.
echo Building frontend...
cd frontend
call npm install
call npm run build
cd ..

REM Run PyInstaller
echo.
echo Building executable with PyInstaller...
pyinstaller desktop\build_desktop.spec --clean

REM Safety: ensure no runtime data is present in bundle output
if exist "dist\Asymptote\data\" (
    echo Removing dist\Asymptote\data\ (runtime user data)...
    rmdir /s /q "dist\Asymptote\data\"
)

REM Create shortcuts
echo.
echo Build complete!
echo.
echo Executable location: dist\Asymptote\Asymptote.exe
echo.
echo To create an installer, run: desktop\build_installer.bat
echo.

pause
