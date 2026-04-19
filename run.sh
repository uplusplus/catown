#!/bin/bash
# Catown - Linux/macOS Launcher
# q=quit  r=restart
#
# Run from any directory. Uvicorn output is visible in this terminal.

set -e

BACKEND="$(cd "$(dirname "$0")/backend" && pwd)"
PID=""
PYTHON_CMD="python3"
PIP_CMD="pip3"

if [ -x "$BACKEND/.venv/bin/python3" ]; then
    PYTHON_CMD="$BACKEND/.venv/bin/python3"
    PIP_CMD="$BACKEND/.venv/bin/pip"
fi

cleanup() {
    if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
        echo "Stopping (PID $PID)..."
        kill "$PID" 2>/dev/null
        wait "$PID" 2>/dev/null || true
    fi
    echo "Done."
    exit 0
}

start_server() {
    echo "Starting Catown..."
    echo "  Web:      http://localhost:8000"
    echo "  API Docs: http://localhost:8000/docs"
    echo ""

    (cd "$BACKEND" && "$PYTHON_CMD" -m uvicorn main:app --reload --host 0.0.0.0 --port 8000) &
    PID=$!
    echo "  PID: $PID"
    echo ""
}

# --- Dependencies ---
if ! command -v "$PYTHON_CMD" &>/dev/null && [ ! -x "$PYTHON_CMD" ]; then
    echo "[ERROR] python3 not found. Install Python 3.10+"
    exit 1
fi

if ! "$PYTHON_CMD" -c "import fastapi, uvicorn" &>/dev/null; then
    echo "Installing dependencies..."
    (cd "$BACKEND" && "$PIP_CMD" install -r requirements.txt)
fi

# --- .env ---
if [ ! -f "$BACKEND/.env" ] && [ -f "$BACKEND/.env.example" ]; then
    cp "$BACKEND/.env.example" "$BACKEND/.env"
    echo "Created backend/.env - edit it to set LLM_API_KEY"
fi

# --- Main ---
trap cleanup EXIT INT TERM

start_server

echo "----------------------------------------------"
echo "  q + Enter  = quit"
echo "  r + Enter  = restart"
echo "  Ctrl+C     = quit"
echo "----------------------------------------------"
echo ""

while true; do
    read -r cmd
    case "$cmd" in
        q|Q) cleanup ;;
        r|R)
            echo "Restarting..."
            if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
                kill "$PID" 2>/dev/null
                wait "$PID" 2>/dev/null || true
            fi
            start_server
            echo "Done."
            echo ""
            ;;
        "") ;;
        *) echo "Unknown: $cmd (q=quit, r=restart)" ;;
    esac
done
