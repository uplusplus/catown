@echo off
REM Catown 启动脚本 (Windows)

echo 🐱 Catown - Multi-Agent Collaboration Platform
echo ==============================================
echo.

REM 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ Python is not installed. Please install Python 3.10+
    pause
    exit /b 1
)

REM 检查 Node.js
node --version >nul 2>&1
if errorlevel 1 (
    echo ❌ Node.js is not installed. Please install Node.js 18+
    pause
    exit /b 1
)

REM 选择启动模式
echo Choose startup mode:
echo 1) Backend only
echo 2) Frontend only  
echo 3) Both (Full stack)
echo.
set /p choice="Enter choice (1-3): "

if "%choice%"=="1" (
    echo Starting backend server...
    cd backend
    python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
) else if "%choice%"=="2" (
    echo Starting frontend development server...
    cd frontend
    npm run dev
) else if "%choice%"=="3" (
    echo Starting full stack...
    
    REM 启动后端
    cd backend
    start "Catown Backend" cmd /k "python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000"
    
    echo Backend started
    echo.
    
    REM 等待后端启动
    timeout /t 3 /nobreak >nul
    
    REM 启动前端
    cd ..\frontend
    start "Catown Frontend" cmd /k "npm run dev"
    
    echo Frontend started
    echo.
    echo ✅ Catown is running!
    echo    Frontend: http://localhost:3000
    echo    Backend:  http://localhost:8000
    echo    API Docs: http://localhost:8000/docs
    echo.
    echo Close the backend and frontend windows to stop
    pause
) else (
    echo Invalid choice
    pause
    exit /b 1
)
