@echo off
setlocal enabledelayedexpansion

echo ============================================
echo   Catown - Multi-Agent Collaboration Platform
echo   Dev Mode (hot reload enabled)
echo ============================================
echo.

set "PROJECT_ROOT=%~dp0"
set "BACKEND_DIR=%PROJECT_ROOT%backend"

REM === Pre-flight: Python only ===

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found, please install Python 3.10+
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set "PYVER=%%v"
echo [INFO] Python %PYVER%

REM === Install dependencies if needed ===

cd /d "%BACKEND_DIR%"
python -c "import fastapi" >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installing Python dependencies...
    pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Failed to install Python dependencies
        pause
        exit /b 1
    )
    echo [OK] Dependencies installed
)

REM === .env setup ===

if not exist "%BACKEND_DIR%\.env" (
    echo [WARN] .env not found, copying from .env.example...
    if exist "%BACKEND_DIR%\.env.example" (
        copy "%BACKEND_DIR%\.env.example" "%BACKEND_DIR%\.env" >nul
        echo [OK] .env created — edit backend\.env to set your LLM_API_KEY
    ) else (
        echo [WARN] No .env.example found, using environment defaults
    )
)

REM === Start server ===

echo.
echo [INFO] Starting Catown...
echo.
echo   Frontend:  http://localhost:8000
echo   API Docs:  http://localhost:8000/docs
echo.
echo   - Python changes:  auto-reload (uvicorn --reload)
echo   - Frontend changes: browser auto-refresh (WebSocket)
echo.
echo   Press Ctrl+C to stop.
echo ============================================
echo.

cd /d "%BACKEND_DIR%"
python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
