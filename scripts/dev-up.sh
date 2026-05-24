#!/usr/bin/env bash
set -euo pipefail

mkdir -p .runtime

docker compose up --build
