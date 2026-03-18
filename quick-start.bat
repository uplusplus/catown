@echo off
REM Catown Quick Start - Windows

echo ========================================
echo Catown Quick Start
echo ========================================
echo.

REM Check Python3
python3 --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python3 not found. Please install Python 3.10+
    pause
    exit /b 1
)

echo [1/3] Checking backend...
cd backend

REM Install dependencies if needed
if not exist "venv" (
    echo [INFO] Creating virtual environment...
    python3 -m venv venv
    call venv\Scripts\activate
    echo [INFO] Installing dependencies...
    pip install -r requirements.txt
) else (
    call venv\Scripts\activate
)

echo.
echo [2/3] Starting backend server...
echo [INFO] Backend will run on http://localhost:8000
echo [INFO] API docs at http://localhost:8000/docs
echo.

start "Catown Backend" cmd /k "python3 -m uvicorn main:app --reload --host 0.0.0.0 --port 8000"

echo [3/3] Waiting for backend to start...
timeout /t 5 /nobreak >nul

echo.
echo ========================================
echo Backend Started Successfully!
echo ========================================
echo.
echo Next steps:
echo 1. Open http://localhost:8000/docs in browser
echo 2. To start frontend: cd frontend ^&^& npm run dev
echo 3. Then open http://localhost:3000
echo.
echo Press any key to open API docs in browser...
pause >nul
start http://localhost:8000/docs

echo.
echo Done! Backend is running.
echo Close this window to stop.
pause
