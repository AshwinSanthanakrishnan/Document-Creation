#!/usr/bin/env bash
# One-command demo: generate letters for every client, then open the run report.
# Usage: ./demo.sh            (extra options pass through, e.g. ./demo.sh --offline)
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo "Setting up for the first time..."
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi

if [ ! -f .env ]; then
  echo "No .env file found. Copy .env.example to .env and add your Gemini API key,"
  echo "or run ./demo.sh --offline to use the standard paragraphs instead."
fi

.venv/bin/python main.py "$@"

if [ "$(uname)" = "Darwin" ]; then
  open outputs/run_report.pdf
  open outputs
fi
