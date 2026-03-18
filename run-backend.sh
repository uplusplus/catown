#!/bin/bash
# Catown Backend Start Script for Linux/Mac

echo "========================================"
echo "Catown Backend Server"
echo "========================================"
echo ""

cd backend

# Check Python3
if ! command -v python3 &> /dev/null; then
    echo "[ERROR] Python3 not found!"
    echo "Please install Python 3.10 or higher."
    exit 1
fi

echo "[Step 1] Running tests..."
python3 test_backend.py
if [ $? -ne 0 ]; then
    echo ""
    echo "[ERROR] Tests failed! Please check the errors above."
    exit 1
fi

echo ""
echo "[Step 2] Starting server..."
echo ""
echo "Backend will be available at:"
echo "  - API:      http://localhost:8000"
echo "  - Docs:     http://localhost:8000/docs"
echo "  - Config:   http://localhost:8000/api/config"
echo "  - Status:   http://localhost:8000/api/status"
echo ""
echo "Press Ctrl+C to stop the server."
echo "========================================"
echo ""

python3 -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
