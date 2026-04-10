@echo off
chcp 65001 >nul 2>&1
REM Catown - Windows Launcher
REM q=quit  r=restart
REM
REM Run from any directory. Uvicorn output is visible in this console.
REM Uses start /B so uvicorn shares this cmd window.
REM PID is captured immediately after launch for clean restart.

setlocal enabledelayedexpansion

set "BACKEND=%~dp0backend"
if "%BACKEND:~-1%"=="\" set "BACKEND=%BACKEND:~0,-1%"

:: --- Python ---
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.10+
    exit /b 1
)

:: --- Dependencies ---
pushd "%BACKEND%"
python -c "import fastapi" >nul 2>&1
if errorlevel 1 (
    echo Installing dependencies...
    pip install -r requirements.txt
)
popd

:: --- .env ---
if not exist "%BACKEND%\.env" (
    if exist "%BACKEND%\.env.example" (
        copy "%BACKEND%\.env.example" "%BACKEND%\.env" >nul
        echo Created backend\.env - edit it to set LLM_API_KEY
    )
)

:: ==========================================
:: Launch
:: ==========================================
:LAUNCH
set "PID="
echo.
echo Starting Catown...
echo   Web:      http://localhost:8000
echo   API Docs: http://localhost:8000/docs

pushd "%BACKEND%"
start "Catown" /B python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
popd

:: Capture PID (retry a few times for startup delay)
for /l %%i in (1,1,10) do (
    if not defined PID (
        call :FIND_PID
        if not defined PID timeout /t 1 /nobreak >nul
    )
)

if defined PID (
    echo   PID:      %PID%
) else (
    echo   PID:      unknown
)
echo.
echo ----------------------------------------------
echo   q + Enter  = quit
echo   r + Enter  = restart
echo ----------------------------------------------
echo.

:: ==========================================
:: Command loop
:: ==========================================
:LOOP
set "cmd="
set /p "cmd=? "
if "!cmd!"=="" goto :LOOP
if /i "!cmd!"=="q" goto :QUIT
if /i "!cmd!"=="r" goto :RESTART
goto :LOOP

:: ==========================================
:: Restart
:: ==========================================
:RESTART
echo Restarting...
if defined PID taskkill /PID %PID% /F >nul 2>&1
set "PID="
timeout /t 1 /nobreak >nul
goto :LAUNCH

:: ==========================================
:: Quit
:: ==========================================
:QUIT
echo Stopping...
if defined PID taskkill /PID %PID% /F >nul 2>&1
set "PID="
echo Done.
exit /b 0

:: ==========================================
:: Find PID of our uvicorn process
:: ==========================================
:FIND_PID
set "PID="
for /f "tokens=2 delims=," %%p in (
    'tasklist /FI "IMAGENAME eq python.exe" /FI "CMDLINE eq *uvicorn main:app*" /FO CSV /NH 2^>nul'
) do (
    set "raw=%%~p"
    if not "!raw!"=="" set "PID=!raw!"
)
exit /b 0
