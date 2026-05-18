@echo off
chcp 65001 >nul 2>&1
REM Catown - Windows Launcher
REM q=quit  r=restart
REM
REM Run from any directory. Uvicorn output is visible in this console.
REM PID is captured immediately after launch for clean restart.

setlocal enabledelayedexpansion

set "BACKEND=%~dp0backend"
if "%BACKEND:~-1%"=="\" set "BACKEND=%BACKEND:~0,-1%"
set "PYTHON_CMD=python"
if not defined CATOWN_HOME set "CATOWN_HOME=%USERPROFILE%\.catown"
if not defined CATOWN_CONFIG_DIR set "CATOWN_CONFIG_DIR=%CATOWN_HOME%\config"
if not defined CATOWN_STATE_DIR set "CATOWN_STATE_DIR=%CATOWN_HOME%\state"
if not defined CATOWN_PROJECTS_ROOT set "CATOWN_PROJECTS_ROOT=%CATOWN_HOME%\projects"
if not defined CATOWN_WORKSPACES_DIR set "CATOWN_WORKSPACES_DIR=%CATOWN_HOME%\workspaces"
set "CATOWN_ENV_FILE=%CATOWN_HOME%\.env"
set "RUN_RELOAD_FLAG="
if "%CATOWN_RELOAD%"=="1" set "RUN_RELOAD_FLAG=--reload"

if not exist "%CATOWN_HOME%" mkdir "%CATOWN_HOME%" >nul 2>&1
if not exist "%CATOWN_CONFIG_DIR%" mkdir "%CATOWN_CONFIG_DIR%" >nul 2>&1
if not exist "%CATOWN_STATE_DIR%" mkdir "%CATOWN_STATE_DIR%" >nul 2>&1
if not exist "%CATOWN_PROJECTS_ROOT%" mkdir "%CATOWN_PROJECTS_ROOT%" >nul 2>&1
if not exist "%CATOWN_WORKSPACES_DIR%" mkdir "%CATOWN_WORKSPACES_DIR%" >nul 2>&1

if not exist "%CATOWN_ENV_FILE%" (
    if exist "%BACKEND%\.env.example" (
        copy "%BACKEND%\.env.example" "%CATOWN_ENV_FILE%" >nul
        echo Created %CATOWN_ENV_FILE% - edit it to set LLM_API_KEY / LLM_BASE_URL / LLM_MODEL
    )
)

for %%F in (agents.json pipelines.json skills.json) do (
    if not exist "%CATOWN_CONFIG_DIR%\%%F" (
        if exist "%BACKEND%\configs\%%F" (
            copy "%BACKEND%\configs\%%F" "%CATOWN_CONFIG_DIR%\%%F" >nul
            echo Installed %CATOWN_CONFIG_DIR%\%%F
        )
    )
)

set "RUN_PORT=%PORT%"
if not defined RUN_PORT if exist "%CATOWN_ENV_FILE%" (
    for /f "usebackq tokens=1,* delims==" %%A in (`findstr /B /C:"PORT=" "%CATOWN_ENV_FILE%"`) do (
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
    call :INSTALL_DEPS
    if errorlevel 1 (
        popd
        echo [ERROR] Dependency installation failed.
        echo         PyPI may be blocked by your network or SSL inspection.
        echo         Try setting a mirror before retrying:
        echo         set PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
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

:: ==========================================
:: Launch
:: ==========================================
:LAUNCH
set "PID="
echo.
echo Starting Catown...
echo   Web:      http://localhost:%RUN_PORT%
echo   API Docs: http://localhost:%RUN_PORT%/docs
if defined RUN_RELOAD_FLAG echo   Reload:   enabled

pushd "%BACKEND%"
start "Catown" /B %PYTHON_CMD% -m uvicorn main:app %RUN_RELOAD_FLAG% --host 0.0.0.0 --port %RUN_PORT%
popd

:: Capture PID (retry a few times for startup delay)
for /l %%i in (1,1,10) do (
    if not defined PID (
        call :FIND_PID
        if not defined PID call :SLEEP_ONE
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
if errorlevel 1 goto :QUIT
if "!cmd!"=="" goto :LOOP
if /i "!cmd!"=="q" goto :QUIT
if /i "!cmd!"=="r" goto :RESTART
goto :LOOP

:: ==========================================
:: Restart
:: ==========================================
:RESTART
echo Restarting...
call :STOP_SERVER
call :SLEEP_ONE
goto :LAUNCH

:: ==========================================
:: Quit
:: ==========================================
:QUIT
echo Stopping...
call :STOP_SERVER
echo Done.
exit /b 0

:: ==========================================
:: Stop all uvicorn processes for the current port
:: ==========================================
:STOP_SERVER
if defined PID taskkill /PID %PID% /T /F >nul 2>&1
call :KILL_UVICORN_BY_PORT
call :SLEEP_ONE
call :KILL_UVICORN_BY_PORT
set "PID="
exit /b 0

:SLEEP_ONE
powershell -NoProfile -Command "Start-Sleep -Seconds 1" >nul 2>&1
exit /b 0

:KILL_UVICORN_BY_PORT
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter 'Name = ''python.exe''' | Where-Object { $_.CommandLine -like '*uvicorn main:app*' -and $_.CommandLine -like '*--port %RUN_PORT%*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
exit /b 0

:: ==========================================
:: Install Python dependencies with mirror fallback
:: ==========================================
:INSTALL_DEPS
%PYTHON_CMD% -m pip install -r requirements.txt
if not errorlevel 1 exit /b 0

if defined PIP_INDEX_URL (
    echo [ERROR] pip install failed with PIP_INDEX_URL=%PIP_INDEX_URL%
    exit /b 1
)

echo Default PyPI failed. Retrying with Tsinghua mirror...
%PYTHON_CMD% -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
if not errorlevel 1 exit /b 0

echo Tsinghua mirror failed. Retrying with Aliyun mirror...
%PYTHON_CMD% -m pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com
if not errorlevel 1 exit /b 0

exit /b 1

:: ==========================================
:: Find PID of our uvicorn process
:: ==========================================
:FIND_PID
set "PID="
for /f "usebackq delims=" %%p in (
    `powershell -NoProfile -Command "$p = Get-CimInstance Win32_Process -Filter 'Name = ''python.exe''' | Where-Object { $_.CommandLine -like '*uvicorn main:app*' -and $_.CommandLine -like '*--port %RUN_PORT%*' } | Select-Object -First 1 -ExpandProperty ProcessId; if ($p) { Write-Output $p }" 2^>nul`
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
:: Prefer an explicit CATOWN_PYTHON, then common Conda installs,
:: then PATH python / py launcher for first-install machines.
:: ==========================================
:RESOLVE_PYTHON
if defined CATOWN_PYTHON (
    call :TRY_SUPPORTED_PYTHON "%CATOWN_PYTHON%"
    if not errorlevel 1 exit /b 0
    echo [ERROR] CATOWN_PYTHON is set but is not a supported Python 3.10+: %CATOWN_PYTHON%
    exit /b 1
)

if exist "%USERPROFILE%\miniconda3\python.exe" (
    call :TRY_SUPPORTED_PYTHON "%USERPROFILE%\miniconda3\python.exe"
    if not errorlevel 1 exit /b 0
)
if exist "%USERPROFILE%\anaconda3\python.exe" (
    call :TRY_SUPPORTED_PYTHON "%USERPROFILE%\anaconda3\python.exe"
    if not errorlevel 1 exit /b 0
)

call :TRY_SUPPORTED_PYTHON python
if not errorlevel 1 exit /b 0

for %%v in (3.15 3.14 3.13 3.12 3.11 3.10) do (
    call :TRY_SUPPORTED_PYTHON py -%%v
    if not errorlevel 1 exit /b 0
)

exit /b 1

:TRY_SUPPORTED_PYTHON
set "CANDIDATE=%*"
%CANDIDATE% -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 exit /b 1
set "PYTHON_CMD=%CANDIDATE%"
exit /b 0
