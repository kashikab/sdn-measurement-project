#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -x .venv/bin/python ]]; then
  echo "Missing .venv: set up the Python environment first (see README)." >&2
  exit 1
fi
sudo mn -c
exec sudo env "PATH=$PWD/.venv/bin:$PATH" .venv/bin/python web/server.py
