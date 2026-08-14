#!/bin/bash
# Finn API startup script

echo "Starting Finn API..."
echo ""

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python -m venv venv
fi

# Activate virtual environment
echo "Activating virtual environment..."
source venv/bin/activate

# Install dependencies if needed
if [ ! -f "venv/.installed" ]; then
    echo "Installing dependencies..."
    pip install -r requirements.txt
    touch venv/.installed
fi

# Create .env if it doesn't exist
if [ ! -f ".env" ]; then
    echo "Creating .env file from template..."
    cp .env.example .env
fi

# Auto-generate self-signed dev cert if none exists
if [ ! -f "certs/server.crt" ]; then
    echo ""
    echo "No TLS certificate found. Generating self-signed dev cert..."
    bash certs/generate-cert.sh
    echo ""
    echo "To enable HTTPS, add these lines to your .env:"
    echo "    SSL_CERTFILE=certs/server.crt"
    echo "    SSL_KEYFILE=certs/server.key"
    echo ""
fi

# Report the port the app will actually bind: PORT in .env, else the config
# default. Hardcoding this drifts the moment someone overrides PORT.
APP_PORT="$(grep -E '^[[:space:]]*PORT=' .env 2>/dev/null | tail -n1 | cut -d= -f2 | tr -d '[:space:]\r')"
APP_PORT="${APP_PORT:-8000}"

# Start the server
echo ""
echo "Starting server on port $APP_PORT  ->  http://localhost:$APP_PORT"
echo "(HTTPS if SSL_CERTFILE is set in .env, otherwise HTTP)"
echo ""
python main.py
