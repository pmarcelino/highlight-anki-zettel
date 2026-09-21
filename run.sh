#!/usr/bin/env bash
# Starts the companion server on 127.0.0.1:8766 (bootstraps .venv on first run).
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "No .env found. Copy .env.example to .env and add your ANTHROPIC_API_KEY." >&2
  exit 1
fi

if [ ! -d .venv ]; then
  echo "Bootstrapping virtual environment..."
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi

exec .venv/bin/uvicorn highlightanki.main:app --app-dir server --host 127.0.0.1 --port 8766
