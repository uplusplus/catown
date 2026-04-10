@echo off
REM Catown 交互式启动脚本 - Windows（同窗口运行）
REM q = 停止, r = 重载

setlocal enabledelayedexpansion
set "DIR=%~dp0backend"
set "PIDFILE=%~dp0.catown.pid"

REM Python 检查
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] 找不到 Python，请安装 Python 3.10+
    pause
    exit /b 1
)

REM 依赖安装
python -c "import fastapi" >nul 2>&1
if errorlevel 1 (
    echo 安装依赖...
    pushd "%DIR%"
    pip install -r requirements.txt
    popd
)

REM .env 初始化
if not exist "%DIR%\.env" (
    if exist "%DIR%\.env.example" (
        copy "%DIR%\.env.example" "%DIR%\.env" >nul
        echo 已创建 backend\.env — 请编辑填入 LLM_API_KEY
    )
)

:START
echo.
echo 启动 Catown...
echo    Frontend:  http://localhost:8000
echo    API Docs:  http://localhost:8000/docs
echo.

REM 用 start /B 后台启动 uvicorn
cd /d "%DIR%"
start "" /B python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000

REM 获取后台进程 PID
for /f "tokens=2 delims=," %%p in (
    'wmic process where "commandline like '%%uvicorn main:app%%' and name='python.exe'" get processid /format:csv 2^>nul ^| findstr /v "Node"'
) do set "PID=%%p"

if not defined PID (
    for /f "tokens=2 delims=," %%p in (
        'wmic process where "commandline like '%%uvicorn%%' and name='python.exe'" get processid /format:csv 2^>nul ^| findstr /v "Node"'
    ) do set "PID=%%p"
)

echo   服务已启动 ^(PID: %PID%^).
echo.
echo ──────────────────────────────────────
echo   输入 q + 回车 = 停止
echo   输入 r + 回车 = 重载（重启）
echo ──────────────────────────────────────
echo.

:LOOP
set "cmd="
set /p "cmd=? "
if /i "!cmd!"=="q" goto :STOP
if /i "!cmd!"=="r" goto :RELOAD
if "!cmd!"=="" goto :LOOP
echo 未知命令: !cmd! （输入 q 停止, r 重载）
goto :LOOP

:RELOAD
echo.
echo 重载中...
if defined PID taskkill /PID %PID% /F >nul 2>&1
timeout /t 1 /nobreak >nul
set "PID="
goto :START

:STOP
echo.
echo 停止服务...
if defined PID taskkill /PID %PID% /F >nul 2>&1
set "PID="
echo 已退出。
pause
exit /b 0
