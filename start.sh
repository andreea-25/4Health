#!/usr/bin/env bash
# 4Health - start the app (Linux / macOS).   Usage: ./start.sh
set -euo pipefail
cd "$(dirname "$0")/api"
[ -x .venv/bin/python ] || { echo "Not installed yet - run ./setup.sh first."; exit 1; }
echo "4Health is running on http://localhost:8000  (API docs: /docs). Press Ctrl+C to stop."
exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
