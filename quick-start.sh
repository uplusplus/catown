#!/bin/bash
# Catown Quick Start - Linux/Mac

set -e

echo "========================================"
echo "Catown Quick Start"
echo "========================================"
echo ""

if ! command -v python3 &> /dev/null; then
    echo "[ERROR] Python3 not found. Please install Python 3.10+"
    exit 1
fi

cd "$(dirname "$0")/backend"

# Install dependencies if needed
if ! python3 -c "import fastapi" &> /dev/null; then
    echo "[INFO] Installing dependencies..."
    pip3 install -r requirements.txt
fi

# .env setup
if [ ! -f ".env" ] && [ -f ".env.example" ]; then
    cp .env.example .env
    echo "[OK] .env created — edit backend/.env to set your LLM_API_KEY"
fi

echo ""
echo "Starting Catown..."
echo ""
echo "  Frontend:  http://localhost:8000"
echo "  API Docs:  http://localhost:8000/docs"
echo ""
echo "  Press Ctrl+C to stop."
echo ""

python3 -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
