#!/usr/bin/env bash
set -e

echo "=========================================================="
echo " Starting FastAPI Gateway on http://localhost:8000"
echo "=========================================================="
uvicorn src.api.app:app --host 0.0.0.0 --port 8000 --reload
