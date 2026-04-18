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
set "PYTHON_CMD=python"
set "RUN_PORT=%PORT%"

if not defined RUN_PORT if exist "%BACKEND%\.env" (
    for /f "usebackq tokens=1,* delims==" %%A in (`findstr /B /C:"PORT=" "%BACKEND%\.env"`) do (
        set "RUN_PORT=%%B"
    )
)

if not defined RUN_PORT set "RUN_PORT=8000"

:: --- Python ---
call :RESOLVE_PYTHON
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.10+
    exit /b 1
)
for /f "usebackq delims=" %%v in (`%PYTHON_CMD% --version 2^>^&1`) do set "PYTHON_VERSION=%%v"
echo Using %PYTHON_VERSION%

:: --- Dependencies ---
pushd "%BACKEND%"
%PYTHON_CMD% -c "import fastapi" >nul 2>&1
if errorlevel 1 (
    echo Installing dependencies...
    %PYTHON_CMD% -m pip install -r requirements.txt
    if errorlevel 1 (
        popd
        echo [ERROR] Dependency installation failed.
        echo         Check your network or pip index configuration, then retry.
        exit /b 1
    )
)
%PYTHON_CMD% -c "import fastapi,uvicorn" >nul 2>&1
if errorlevel 1 (
    popd
    echo [ERROR] fastapi/uvicorn still unavailable in the current Python environment.
    echo         Try running: %PYTHON_CMD% -m pip install -r backend\requirements.txt
    exit /b 1
)
popd

call :FIND_AVAILABLE_PORT
if errorlevel 1 (
    echo [ERROR] No available port found near %RUN_PORT%.
    exit /b 1
)

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
echo   Web:      http://localhost:%RUN_PORT%
echo   API Docs: http://localhost:%RUN_PORT%/docs

pushd "%BACKEND%"
start "Catown" /B %PYTHON_CMD% -m uvicorn main:app --reload --host 0.0.0.0 --port %RUN_PORT%
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
    echo [ERROR] Server process did not start successfully.
    echo         Check the log output above for the import or config error.
    exit /b 1
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
for /f "usebackq delims=" %%p in (
    `powershell -NoProfile -Command "$p = Get-CimInstance Win32_Process -Filter \"Name = 'python.exe'\" | Where-Object { $_.CommandLine -like '*uvicorn main:app*' -and $_.CommandLine -like '*--port %RUN_PORT%*' } | Select-Object -First 1 -ExpandProperty ProcessId; if ($p) { Write-Output $p }" 2^>nul`
) do (
    if not "%%~p"=="" set "PID=%%~p"
)
exit /b 0

:: ==========================================
:: Find an available port starting from RUN_PORT
:: ==========================================
:FIND_AVAILABLE_PORT
set /a PORT_END=%RUN_PORT%+20
for /l %%p in (%RUN_PORT%,1,%PORT_END%) do (
    call :PORT_IS_FREE %%p
    if not errorlevel 1 (
        if not "%%p"=="%RUN_PORT%" (
            echo [WARN] Port %RUN_PORT% is unavailable. Falling back to %%p.
        )
        set "RUN_PORT=%%p"
        exit /b 0
    )
)
exit /b 1

:: ==========================================
:: Return 0 when the TCP port can be listened on
:: ==========================================
:PORT_IS_FREE
powershell -NoProfile -Command "$listener = $null; try { $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, %1); $listener.Start(); exit 0 } catch { exit 1 } finally { if ($listener) { $listener.Stop() } }" >nul 2>&1
exit /b %errorlevel%

:: ==========================================
:: Resolve a supported Python runtime
:: Prefer the current PATH python if it is 3.10+,
:: otherwise try the Windows launcher.
:: ==========================================
:RESOLVE_PYTHON
python -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_CMD=python"
    exit /b 0
)

for %%v in (3.15 3.14 3.13 3.12 3.11 3.10) do (
    py -%%v -c "import sys; raise SystemExit(0)" >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_CMD=py -%%v"
        exit /b 0
    )
)

exit /b 1
