#!/bin/bash
# Catown Quick Start - Linux/Mac

echo "========================================"
echo "Catown Quick Start"
echo "========================================"
echo ""

# Check Python3
if ! command -v python3 &> /dev/null; then
    echo "[ERROR] Python3 not found. Please install Python 3.10+"
    exit 1
fi

echo "[1/3] Checking backend..."
cd backend

# Install dependencies if needed
if [ ! -d "venv" ]; then
    echo "[INFO] Creating virtual environment..."
    python3 -m venv venv
    source venv/bin/activate
    echo "[INFO] Installing dependencies..."
    pip install -r requirements.txt
else
    source venv/bin/activate
fi

echo ""
echo "[2/3] Starting backend server..."
echo "[INFO] Backend will run on http://localhost:8000"
echo "[INFO] API docs at http://localhost:8000/docs"
echo ""

# Start backend
python3 -m uvicorn main:app --reload --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!

echo "[3/3] Waiting for backend to start..."
sleep 5

echo ""
echo "========================================"
echo "Backend Started Successfully!"
echo "========================================"
echo ""
echo "Backend PID: $BACKEND_PID"
echo ""
echo "Next steps:"
echo "1. Open http://localhost:8000/docs in browser"
echo "2. To start frontend: cd frontend && npm run dev"
echo "3. Then open http://localhost:3000"
echo ""
echo "Press Ctrl+C to stop backend"
echo ""

# Wait for interrupt
trap "kill $BACKEND_PID 2>/dev/null; echo 'Backend stopped.'; exit" INT
wait
