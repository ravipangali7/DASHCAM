@echo off
REM Video File Server Startup Script for Windows
REM This script starts the Python video file server

echo ============================================================
echo Video File Player Server
echo ============================================================
echo.

REM Get the directory where this script is located
cd /d "%~dp0"

REM Check if Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in PATH
    echo Please install Python from https://www.python.org/
    pause
    exit /b 1
)

echo [INFO] Starting video file server on port 2223...
echo [INFO] Access the player at: http://localhost:2223
echo [INFO] Press Ctrl+C to stop the server
echo.
echo ============================================================
echo.

REM Check if web_server.py exists (preferred - has full device API)
if exist "web_server.py" (
    python web_server.py
) else (
    if exist "start_video_server.py" (
        echo [WARNING] Using start_video_server.py (limited functionality - no device API)
        echo [WARNING] For full features including device API, use web_server.py
        python start_video_server.py
    ) else (
        echo [ERROR] Neither web_server.py nor start_video_server.py found!
        echo Please make sure you're running this from the project directory.
        pause
        exit /b 1
    )
)

REM If we get here, the server stopped
echo.
echo [INFO] Server stopped.
pause
