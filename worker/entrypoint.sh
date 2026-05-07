#!/bin/bash
set -e

# Start Xvfb virtual display (required for Chrome extensions on headless Linux)
Xvfb :99 -screen 0 1920x1080x24 -ac +extension GLX +render -noreset &
export DISPLAY=:99

echo "✅ Xvfb started on :99"

# Wait for display to be ready
sleep 1

# Start FastAPI Worker
exec uvicorn main:app --host 0.0.0.0 --port 8080 --workers 1 --log-level info
