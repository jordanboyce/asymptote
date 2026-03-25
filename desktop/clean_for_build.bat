@echo off
REM Pre-build cleanup script for Asymptote Desktop
REM Run this before building to ensure no sensitive data is included
REM
REM This script removes:
REM   - User data (indexed documents, collections)
REM   - API keys stored in app.db
REM   - Backup files
REM   - Any .env file (use .env.example as template)

echo ========================================
echo Asymptote Pre-Build Cleanup
echo ========================================
echo.

REM Go to project root
cd /d "%~dp0\.."

echo WARNING: This will delete all user data from the data/ directory.
echo This includes:
echo   - Indexed documents and collections
echo   - Stored API keys (app.db)
echo   - Backup files
echo.
set /p confirm="Are you sure you want to continue? (y/N): "
if /i not "%confirm%"=="y" (
    echo Cleanup cancelled.
    pause
    exit /b 0
)

echo.
echo Cleaning up...

REM Remove app database (contains API keys and user preferences)
if exist "data\app.db" (
    echo Removing data\app.db (contains API keys)...
    del /f "data\app.db"
)

REM Remove all collection data
if exist "data\default" (
    echo Removing data\default\ (default collection data)...
    rmdir /s /q "data\default"
)

REM Remove any other collection directories (non-default)
for /d %%D in (data\*) do (
    if /i not "%%~nxD"=="backups" (
        echo Removing data\%%~nxD\ ...
        rmdir /s /q "%%D"
    )
)

REM Remove backup files
if exist "data\backups" (
    echo Removing data\backups\ ...
    rmdir /s /q "data\backups"
)

REM Remove .env if it exists (should use .env.example)
if exist ".env" (
    echo Removing .env file...
    del /f ".env"
)

REM Remove any __pycache__ directories
echo Removing __pycache__ directories...
for /d /r %%D in (__pycache__) do (
    if exist "%%D" rmdir /s /q "%%D"
)

REM Remove any .pyc files
echo Removing .pyc files...
del /s /q *.pyc 2>nul

REM Recreate empty data directory structure
echo.
echo Recreating empty data directory...
if not exist "data" mkdir "data"

echo.
echo ========================================
echo Cleanup complete!
echo ========================================
echo.
echo The following have been removed:
echo   [x] data/app.db (API keys, preferences)
echo   [x] data/default/ (default collection)
echo   [x] data/backups/ (backup files)
echo   [x] .env (environment config)
echo   [x] __pycache__ directories
echo   [x] .pyc files
echo.
echo You can now safely run build_windows.bat
echo.

pause
