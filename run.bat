@echo off
setlocal EnableExtensions
cd /d "%~dp0"

rem ============================================================
rem  Time Tracker - one-click launcher (Windows)
rem  Usage:  run.bat            start server + open browser
rem          run.bat --no-open  start server only
rem ============================================================
rem  NOTE: this file must stay ASCII-only.
rem  cmd.exe reads batch files with the ANSI codepage, so any
rem  non-ASCII characters break line parsing.
rem ============================================================

echo ==========================================
echo    Time Tracker - starting server
echo ==========================================

where python >nul 2>nul
if errorlevel 1 goto no_python

if not exist ".venv\Scripts\activate.bat" goto create_venv
goto check_deps

:create_venv
echo.
echo Creating virtual environment .venv ...
python -m venv .venv
if errorlevel 1 goto venv_fail

:check_deps
call ".venv\Scripts\activate.bat"
python -c "import flask, bcrypt, dotenv" >nul 2>nul
if errorlevel 1 goto install_deps
goto run_server

:install_deps
echo.
echo Installing dependencies from requirements.txt ...
python -m pip install -r requirements.txt
if errorlevel 1 goto install_fail

:run_server
rem Port is taken from .env when present, otherwise 5000
set "APP_PORT=5000"
if exist ".env" for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do if /i "%%A"=="PORT" set "APP_PORT=%%B"

echo.
echo Server is starting:  http://127.0.0.1:%APP_PORT%
echo Keep this window open while you are using the tracker.
echo Press Ctrl+C or close the window to stop the server.
echo.
if /i not "%~1"=="--no-open" start "" "http://127.0.0.1:%APP_PORT%"

python main.py
echo.
echo Server stopped.
pause
exit /b 0

:no_python
echo.
echo [ERROR] Python was not found in PATH.
echo Install Python 3.10+ from https://www.python.org/downloads/
echo Make sure to tick "Add python.exe to PATH" during setup.
pause
exit /b 1

:venv_fail
echo.
echo [ERROR] Failed to create the .venv environment.
pause
exit /b 1

:install_fail
echo.
echo [ERROR] Failed to install dependencies.
echo Check your internet connection and run this file again.
pause
exit /b 1
