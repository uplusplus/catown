#!/bin/bash
# Catown - Linux/macOS Launcher
# q=quit  r=restart
#
# Run from any directory. Uvicorn output is visible in this terminal.

set -e

BACKEND="$(cd "$(dirname "$0")/backend" && pwd)"
PID=""
RUN_HOST="${RUN_HOST:-0.0.0.0}"
RUN_PORT="${RUN_PORT:-8000}"
BASE_PYTHON="${PYTHON:-python3}"
PYTHON_CMD=""
VENV_DIR="${CATOWN_VENV_DIR:-}"
CATOWN_HOME="${CATOWN_HOME:-$HOME/.catown}"
CATOWN_CONFIG_DIR="${CATOWN_CONFIG_DIR:-$CATOWN_HOME/config}"
CATOWN_STATE_DIR="${CATOWN_STATE_DIR:-$CATOWN_HOME/state}"
CATOWN_PROJECTS_ROOT="${CATOWN_PROJECTS_ROOT:-$CATOWN_HOME/projects}"
CATOWN_WORKSPACES_DIR="${CATOWN_WORKSPACES_DIR:-$CATOWN_HOME/workspaces}"
CATOWN_ENV_FILE="$CATOWN_HOME/.env"

cleanup() {
    if [ -n "$PID" ]; then
        echo "Stopping (PID $PID)..."
    fi
    stop_server
    echo "Done."
    exit 0
}

port_in_use() {
    "$BASE_PYTHON" - "$RUN_PORT" <<'PY'
import socket
import sys

port = int(sys.argv[1])
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    sock.bind(("0.0.0.0", port))
except OSError:
    raise SystemExit(0)
finally:
    sock.close()
raise SystemExit(1)
PY
}

find_port_pids() {
    if command -v lsof >/dev/null 2>&1; then
        lsof -tiTCP:"$RUN_PORT" -sTCP:LISTEN 2>/dev/null | sort -u
        return
    fi
    if command -v fuser >/dev/null 2>&1; then
        fuser "$RUN_PORT"/tcp 2>/dev/null | tr ' ' '\n' | sed '/^$/d' | sort -u
    fi
}

wait_for_port_free() {
    local deadline=$((SECONDS + ${1:-10}))
    while port_in_use; do
        if [ "$SECONDS" -ge "$deadline" ]; then
            return 1
        fi
        sleep 0.2
    done
    return 0
}

stop_server() {
    if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
        kill "$PID" 2>/dev/null || true
        wait "$PID" 2>/dev/null || true
    fi
    PID=""

    if wait_for_port_free 5; then
        return
    fi

    echo "Port $RUN_PORT is still in use; terminating leftover listener..."
    local pids
    pids="$(find_port_pids || true)"
    if [ -n "$pids" ]; then
        echo "$pids" | xargs -r kill 2>/dev/null || true
        wait_for_port_free 3 || true
    fi

    if port_in_use && [ -n "$pids" ]; then
        echo "$pids" | xargs -r kill -9 2>/dev/null || true
        wait_for_port_free 2 || true
    fi
}

prepare_runtime_layout() {
    mkdir -p "$CATOWN_HOME" "$CATOWN_CONFIG_DIR" "$CATOWN_STATE_DIR" "$CATOWN_PROJECTS_ROOT" "$CATOWN_WORKSPACES_DIR"

    if [ ! -f "$CATOWN_ENV_FILE" ] && [ -f "$BACKEND/.env.example" ]; then
        cp "$BACKEND/.env.example" "$CATOWN_ENV_FILE"
        echo "Created $CATOWN_ENV_FILE - edit it to set LLM_API_KEY / LLM_BASE_URL / LLM_MODEL"
    fi

    for config_name in agents.json pipelines.json skills.json; do
        if [ ! -f "$CATOWN_CONFIG_DIR/$config_name" ] && [ -f "$BACKEND/configs/$config_name" ]; then
            cp "$BACKEND/configs/$config_name" "$CATOWN_CONFIG_DIR/$config_name"
            echo "Installed $CATOWN_CONFIG_DIR/$config_name"
        fi
    done
}

is_wsl_windows_mount() {
    case "$(uname -r 2>/dev/null || true)" in
        *[Mm]icrosoft*|*WSL*)
            case "$BACKEND" in
                /mnt/*) return 0 ;;
            esac
            ;;
    esac
    return 1
}

resolve_venv_dir() {
    if [ -n "$VENV_DIR" ]; then
        return
    fi

    if is_wsl_windows_mount; then
        VENV_DIR="$CATOWN_HOME/venv"
    else
        VENV_DIR="$BACKEND/.venv"
    fi
}

ensure_base_python() {
    if ! command -v "$BASE_PYTHON" &>/dev/null; then
        echo "[ERROR] $BASE_PYTHON not found. Install Python 3.10+"
        exit 1
    fi

    if ! "$BASE_PYTHON" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" &>/dev/null; then
        echo "[ERROR] Python 3.10+ is required. Found: $($BASE_PYTHON --version 2>&1)"
        exit 1
    fi
}

bootstrap_pip() {
    if "$PYTHON_CMD" -m pip --version &>/dev/null; then
        return
    fi

    echo "Bootstrapping pip..."
    if "$PYTHON_CMD" -m ensurepip --upgrade &>/dev/null; then
        return
    fi

    if "$BASE_PYTHON" -m pip --help 2>/dev/null | grep -q -- "--python"; then
        "$BASE_PYTHON" -m pip --python "$VENV_DIR" install --upgrade pip
        return
    fi

    echo "[ERROR] pip is not available for the virtual environment."
    echo "        Install python3-venv / ensurepip, or set CATOWN_VENV_DIR to an existing venv."
    exit 1
}

ensure_virtualenv() {
    resolve_venv_dir
    mkdir -p "$(dirname "$VENV_DIR")"

    if [ ! -x "$VENV_DIR/bin/python3" ] && [ ! -x "$VENV_DIR/bin/python" ]; then
        echo "Creating virtual environment: $VENV_DIR"
        if ! "$BASE_PYTHON" -m venv "$VENV_DIR"; then
            echo "Retrying virtual environment creation without bundled pip..."
            "$BASE_PYTHON" -m venv --without-pip "$VENV_DIR"
        fi
    fi

    if [ -x "$VENV_DIR/bin/python3" ]; then
        PYTHON_CMD="$VENV_DIR/bin/python3"
    elif [ -x "$VENV_DIR/bin/python" ]; then
        PYTHON_CMD="$VENV_DIR/bin/python"
    else
        echo "[ERROR] Virtual environment is missing python: $VENV_DIR"
        exit 1
    fi

    bootstrap_pip
}

install_dependencies() {
    if ! "$PYTHON_CMD" -c "import fastapi, uvicorn" &>/dev/null; then
        echo "Installing dependencies into $VENV_DIR..."
        (cd "$BACKEND" && "$PYTHON_CMD" -m pip install -r requirements.txt)
    fi
}

start_server() {
    echo "Starting Catown..."
    echo "  Web:      http://localhost:$RUN_PORT"
    echo "  API Docs: http://localhost:$RUN_PORT/docs"
    echo ""

    if ! wait_for_port_free 10; then
        echo "[ERROR] Port $RUN_PORT is still in use. Stop the existing server and retry."
        return 1
    fi

    (cd "$BACKEND" && "$PYTHON_CMD" -m uvicorn main:app --reload --host "$RUN_HOST" --port "$RUN_PORT" --timeout-graceful-shutdown 5 < /dev/null) &
    PID=$!
    echo "  PID: $PID"
    echo ""
}

# --- Dependencies ---
ensure_base_python
ensure_virtualenv
install_dependencies

# --- Runtime data ---
export CATOWN_HOME CATOWN_CONFIG_DIR CATOWN_STATE_DIR CATOWN_PROJECTS_ROOT CATOWN_WORKSPACES_DIR
prepare_runtime_layout

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
    if ! read -r cmd; then
        cleanup
    fi
    case "$cmd" in
        q|Q) cleanup ;;
        r|R)
            echo "Restarting..."
            stop_server
            start_server || true
            echo "Done."
            echo ""
            ;;
        "") ;;
        *) echo "Unknown: $cmd (q=quit, r=restart)" ;;
    esac
done
