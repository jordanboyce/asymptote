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

REM Start the server
echo.
echo Starting server on http://localhost:8000
echo Interactive docs: http://localhost:8000/docs
echo.
python main.py
