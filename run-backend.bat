@echo off
REM Catown Backend Start Script - Windows

echo ========================================
echo Catown Backend Server
echo ========================================
echo.

cd /d "%~dp0backend"

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found! Install Python 3.10+
    pause
    exit /b 1
)

if not exist ".env" (
    if exist ".env.example" (
        copy ".env.example" ".env" >nul
        echo [OK] .env created
    )
)

echo Backend:   http://localhost:8000
echo API Docs:  http://localhost:8000/docs
echo.
echo Press Ctrl+C to stop.
echo ========================================
echo.

python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
