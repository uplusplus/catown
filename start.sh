#!/bin/bash
# Catown — Dev Mode (hot reload enabled)

set -e

echo "============================================"
echo "  Catown - Multi-Agent Collaboration Platform"
echo "  Dev Mode (hot reload enabled)"
echo "============================================"
echo ""

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$PROJECT_ROOT/backend"

# === Pre-flight: Python only ===

if ! command -v python3 &> /dev/null; then
    echo "[ERROR] Python3 not found, please install Python 3.10+"
    exit 1
fi

PYVER=$(python3 --version 2>&1)
echo "[INFO] $PYVER"

# === Install dependencies if needed ===

cd "$BACKEND_DIR"
if ! python3 -c "import fastapi" &> /dev/null; then
    echo "[INFO] Installing Python dependencies..."
    pip3 install -r requirements.txt
    echo "[OK] Dependencies installed"
fi

# === .env setup ===

if [ ! -f "$BACKEND_DIR/.env" ]; then
    echo "[WARN] .env not found, copying from .env.example..."
    if [ -f "$BACKEND_DIR/.env.example" ]; then
        cp "$BACKEND_DIR/.env.example" "$BACKEND_DIR/.env"
        echo "[OK] .env created — edit backend/.env to set your LLM_API_KEY"
    else
        echo "[WARN] No .env.example found, using environment defaults"
    fi
fi

# === Check port ===

if lsof -i :8000 &> /dev/null 2>&1; then
    echo "[ERROR] Port 8000 already in use. Kill it first or change PORT in .env"
    exit 1
fi

# === Start server ===

echo ""
echo "[INFO] Starting Catown..."
echo ""
echo "  Frontend:  http://localhost:8000"
echo "  API Docs:  http://localhost:8000/docs"
echo ""
echo "  - Python changes:  auto-reload (uvicorn --reload)"
echo "  - Frontend changes: browser auto-refresh (WebSocket)"
echo ""
echo "  Press Ctrl+C to stop."
echo "============================================"
echo ""

cd "$BACKEND_DIR"
python3 -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
