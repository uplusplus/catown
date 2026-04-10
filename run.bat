@echo off
REM Catown Interactive Launcher - Windows
REM q = stop, r = reload

setlocal enabledelayedexpansion
set "DIR=%~dp0backend"

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.10+
    pause
    exit /b 1
)

python -c "import fastapi" >nul 2>&1
if errorlevel 1 (
    echo Installing dependencies...
    pushd "%DIR%"
    pip install -r requirements.txt
    popd
)

if not exist "%DIR%\.env" (
    if exist "%DIR%\.env.example" (
        copy "%DIR%\.env.example" "%DIR%\.env" >nul
        echo Created backend\.env - edit to set your LLM_API_KEY
    )
)

:START
echo.
echo Starting Catown...
echo   Frontend:  http://localhost:8000
echo   API Docs:  http://localhost:8000/docs
echo.

cd /d "%DIR%"
start "" /B python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000

for /f "tokens=2 delims=," %%p in (
    'wmic process where "commandline like '%%uvicorn main:app%%' and name='python.exe'" get processid /format:csv 2^>nul ^| findstr /v "Node"'
) do set "PID=%%p"

if not defined PID (
    for /f "tokens=2 delims=," %%p in (
        'wmic process where "commandline like '%%uvicorn%%' and name='python.exe'" get processid /format:csv 2^>nul ^| findstr /v "Node"'
    ) do set "PID=%%p"
)

echo   Server started (PID: %PID%).
echo.
echo ----------------------------------------
echo   q + Enter = stop
echo   r + Enter = reload
echo ----------------------------------------
echo.

:LOOP
set "cmd="
set /p "cmd=>> "
if /i "!cmd!"=="q" goto :STOP
if /i "!cmd!"=="r" goto :RELOAD
if "!cmd!"=="" goto :LOOP
echo Unknown command: !cmd! (q=stop, r=reload)
goto :LOOP

:RELOAD
echo.
echo Reloading...
if defined PID taskkill /PID %PID% /F >nul 2>&1
timeout /t 1 /nobreak >nul
set "PID="
goto :START

:STOP
echo.
echo Stopping...
if defined PID taskkill /PID %PID% /F >nul 2>&1
set "PID="
echo Done.
pause
exit /b 0
