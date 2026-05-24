#!/usr/bin/env bash
set -euo pipefail

if [[ ! -x ".venv/bin/python" ]]; then
  echo "Missing .venv. Run: make install"
  exit 1
fi

mkdir -p .runtime
sudo .venv/bin/python tunnel_helper.py
