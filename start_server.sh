#!/bin/bash
# Video File Server Startup Script for Linux/Mac
# This script starts the Python video file server

echo "============================================================"
echo "Video File Player Server"
echo "============================================================"
echo ""

# Get the directory where this script is located
cd "$(dirname "$0")"

# Check if Python is available
if ! command -v python3 &> /dev/null && ! command -v python &> /dev/null; then
    echo "[ERROR] Python is not installed or not in PATH"
    echo "Please install Python from https://www.python.org/"
    exit 1
fi

# Use python3 if available, otherwise use python
if command -v python3 &> /dev/null; then
    PYTHON_CMD=python3
else
    PYTHON_CMD=python
fi

echo "[INFO] Starting video file server on port 2223..."
echo "[INFO] Access the player at: http://localhost:2223"
echo "[INFO] Press Ctrl+C to stop the server"
echo ""
echo "============================================================"
echo ""

# Check if web_server.py exists (preferred - has full device API)
if [ -f "web_server.py" ]; then
    $PYTHON_CMD web_server.py
elif [ -f "start_video_server.py" ]; then
    echo "[WARNING] Using start_video_server.py (limited functionality - no device API)"
    echo "[WARNING] For full features including device API, use web_server.py"
    $PYTHON_CMD start_video_server.py
else
    echo "[ERROR] Neither web_server.py nor start_video_server.py found!"
    echo "Please make sure you're running this from the project directory."
    exit 1
fi

# If we get here, the server stopped
echo ""
echo "[INFO] Server stopped."
