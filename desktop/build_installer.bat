@echo off
REM Build Windows installer for Asymptote Desktop
REM Requires Inno Setup to be installed

echo ========================================
echo Asymptote Installer Build Script
echo ========================================
echo.

REM Go to parent directory (project root)
cd ..

REM Check if Inno Setup is installed
if not exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" (
    echo ERROR: Inno Setup 6 not found!
    echo Please install from: https://jrsoftware.org/isdl.php
    echo.
    pause
    exit /b 1
)

REM Check if dist folder exists
if not exist "dist\Asymptote\Asymptote.exe" (
    echo ERROR: Asymptote.exe not found in dist\Asymptote\
    echo Please run desktop\build_windows.bat first
    echo.
    pause
    exit /b 1
)

REM Create installer output directory
if not exist "installer_output" mkdir installer_output

REM Sanitize dist output before installer packaging
if exist "dist\Asymptote\data\" (
    echo Removing dist\Asymptote\data\ before packaging installer...
    rmdir /s /q "dist\Asymptote\data\"
)

REM Run Inno Setup
echo.
echo Building installer...
"%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" desktop\installer.iss

if %ERRORLEVEL% EQU 0 (
    echo.
    echo ========================================
    echo Installer built successfully!
    echo Location: installer_output\
    echo ========================================
) else (
    echo.
    echo ERROR: Installer build failed
)

echo.
pause
