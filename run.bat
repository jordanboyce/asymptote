@echo off
REM Asymptote API startup script for Windows

echo Starting Asymptote API...
echo.

REM Check if virtual environment exists
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
)

REM Activate virtual environment
echo Activating virtual environment...
call venv\Scripts\activate.bat

REM Install dependencies if needed
if not exist "venv\.installed" (
    echo Installing dependencies...
    pip install -r requirements.txt
    type nul > venv\.installed
)

REM Create .env if it doesn't exist
if not exist ".env" (
    echo Creating .env file from template...
    copy .env.example .env
)

REM Auto-generate self-signed dev cert if none exists
if not exist "certs\server.crt" (
    echo.
    echo No TLS certificate found. Generating self-signed dev cert...
    bash certs/generate-cert.sh
    echo.
    echo To enable HTTPS, add these lines to your .env:
    echo     SSL_CERTFILE=certs/server.crt
    echo     SSL_KEYFILE=certs/server.key
    echo.
)

REM Start the server
echo.
echo Starting server on port 8473
echo (HTTPS if SSL_CERTFILE is set in .env, otherwise HTTP)
echo.
python main.py
