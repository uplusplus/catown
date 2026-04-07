#!/bin/bash
# Catown Backend Start Script - Linux/Mac

echo "========================================"
echo "Catown Backend Server"
echo "========================================"
echo ""

cd "$(dirname "$0")/backend"

if ! command -v python3 &> /dev/null; then
    echo "[ERROR] Python3 not found! Install Python 3.10+"
    exit 1
fi

if [ ! -f ".env" ] && [ -f ".env.example" ]; then
    cp .env.example .env
    echo "[OK] .env created"
fi

echo "Backend:   http://localhost:8000"
echo "API Docs:  http://localhost:8000/docs"
echo ""
echo "Press Ctrl+C to stop."
echo "========================================"
echo ""

python3 -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
