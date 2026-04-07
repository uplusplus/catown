@echo off
setlocal enabledelayedexpansion

echo ============================================
echo   Catown - Multi-Agent Collaboration Platform
echo ============================================
echo.

set "PROJECT_ROOT=%~dp0"
set "BACKEND_DIR=%PROJECT_ROOT%backend"
set "FRONTEND_DIR=%PROJECT_ROOT%frontend"

REM === Pre-flight checks ===

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found, please install Python 3.10+
    pause
    exit /b 1
)

where node >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Node.js not found, please install Node.js 18+
    pause
    exit /b 1
)

REM === Backend check ===

for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":8000" ^| findstr "LISTENING"') do (
    set "BACKEND_PID=%%a"
)

if defined BACKEND_PID (
    echo [INFO] Backend already running on port 8000 (PID: !BACKEND_PID!)
    set "BACKEND_READY=true"
) else (
    echo [INFO] Port 8000 free, starting backend...
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
    )
    if not exist "%BACKEND_DIR%\.env" (
        echo [WARN] .env not found, copying from .env.example...
        if exist "%BACKEND_DIR%\.env.example" (
            copy "%BACKEND_DIR%\.env.example" "%BACKEND_DIR%\.env" >nul
        ) else (
            echo [WARN] No .env.example either, using defaults
        )
    )
    start "Catown Backend" cmd /c "cd /d %BACKEND_DIR% && python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000"
    set "BACKEND_READY=true"
    echo [OK] Backend started (http://localhost:8000)
    echo      API Docs: http://localhost:8000/docs
)

echo.

REM === Frontend check ===

echo [INFO] Starting frontend...
cd /d "%FRONTEND_DIR%"
if not exist "%FRONTEND_DIR%\node_modules\.bin\vite.cmd" (
    echo [INFO] vite not found, running npm install...
    call npm install
    if errorlevel 1 (
        echo [ERROR] Failed to install frontend dependencies
        pause
        exit /b 1
    )
    echo [OK] Frontend dependencies installed
)
start "Catown Frontend" cmd /c "cd /d %FRONTEND_DIR% && npm run dev"
echo [OK] Frontend started (http://localhost:3000)
echo.

REM === Done ===

echo ============================================
echo   Catown is running!
echo.
echo   Frontend:  http://localhost:3000
echo   Backend:   http://localhost:8000
echo   API Docs:  http://localhost:8000/docs
echo.
echo   Close terminal windows to stop.
echo ===========================================