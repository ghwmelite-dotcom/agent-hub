@echo off
REM Quick-start script for Agent Hub on Windows.
REM Double-click this file or run from a terminal.

setlocal
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
    echo No virtualenv found. Creating one...
    python -m venv .venv
    if errorlevel 1 (
        echo Failed to create virtualenv. Is Python 3.11+ installed and on PATH?
        pause
        exit /b 1
    )
    call .venv\Scripts\activate.bat
    echo Installing dependencies...
    pip install -r requirements.txt
    if errorlevel 1 (
        echo Dependency install failed.
        pause
        exit /b 1
    )
) else (
    call .venv\Scripts\activate.bat
)

if not exist .env (
    echo No .env file found. Copy .env.example to .env and fill in your tokens.
    pause
    exit /b 1
)

python -m agent_hub
pause
