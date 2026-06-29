#!/usr/bin/env bash
# Convenience launcher: set up a venv, install deps, seed demo data, run.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "Creating virtual environment…"
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example (edit it to add an LLM key)."
fi

python -m scripts.seed

echo "Starting server on http://localhost:8000 …"
exec uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
