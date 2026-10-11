#!/usr/bin/env bash
# One-command environment setup for klevs FX trading App.
# (The .venv directory is not stored in git — recreate it with this script.)
set -euo pipefail
cd "$(dirname "$0")"

python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt ruff
# optional live market data (Yahoo Finance hourly bars); the app works without it
.venv/bin/pip install -r requirements-live.txt || echo "warning: live data package not installed — synthetic market will be used"

echo ""
echo "Environment ready. Try:"
echo "  .venv/bin/python -m pytest tests/ -q          # run the test suite"
echo "  .venv/bin/python -m scripts.scan              # scan the FX market"
echo "  .venv/bin/python -m uvicorn fxbot.server:app --host 0.0.0.0 --port 8000"
echo "      -> landing page: http://localhost:8000/   bot: http://localhost:8000/app"
