@echo off
REM Catown Quick Start - Windows

echo ========================================
echo Catown Quick Start
echo ========================================
echo.

REM Check Python3
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.10+
    pause
    exit /b 1
)

cd /d "%~dp0backend"

REM Install dependencies if needed
python -c "import fastapi" >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installing dependencies...
    pip install -r requirements.txt
)

REM .env setup
if not exist ".env" (
    if exist ".env.example" (
        copy ".env.example" ".env" >nul
        echo [OK] .env created — edit backend\.env to set your LLM_API_KEY
    )
)

echo.
echo Starting Catown...
echo.
echo   Frontend:  http://localhost:8000
echo   API Docs:  http://localhost:8000/docs
echo.

start "Catown" cmd /c "python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000"

timeout /t 3 /nobreak >nul
start http://localhost:8000

echo Catown is running! Close this window to stop.
pause
