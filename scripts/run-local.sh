#!/usr/bin/env bash
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

TUNNEL_TIMEOUT="${TUNNEL_TIMEOUT:-30}"

echo -e "${BLUE}=== iPhone GPS Controller Local Run ===${NC}\n"

if [[ ! -x ".venv/bin/python" ]]; then
  echo "Missing .venv. Run: make install"
  exit 1
fi

mkdir -p .runtime

tunnel_running() {
  pgrep -f "tunnel_helper.py" >/dev/null 2>&1
}

stop_tunnel_helper() {
  if [[ -f .runtime/tunnel.pid ]]; then
    local pid
    pid="$(cat .runtime/tunnel.pid 2>/dev/null || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      kill -TERM "$pid" 2>/dev/null || true
      sleep 1
    fi
  fi

  if tunnel_running; then
    pkill -f "tunnel_helper.py" 2>/dev/null || true
    sleep 1
  fi

  rm -f .runtime/tunnel.pid
}

start_tunnel_helper() {
  echo -e "${YELLOW}Checking sudo permission for tunnel helper...${NC}"
  sudo -v

  rm -f .runtime/rsd.json
  : > .runtime/tunnel.log

  echo -e "${YELLOW}Starting tunnel helper in background...${NC}"
  nohup sudo -n -E .venv/bin/python tunnel_helper.py > .runtime/tunnel.log 2>&1 &
  local tunnel_pid=$!
  echo "$tunnel_pid" > .runtime/tunnel.pid
  echo -e "${GREEN}Tunnel helper started (PID: $tunnel_pid)${NC}"
}

ensure_tunnel() {
  if tunnel_running; then
    echo -e "${GREEN}Tunnel helper already running${NC}"
  else
    start_tunnel_helper
  fi

  if .venv/bin/python scripts/wait-for-tunnel.py --timeout "$TUNNEL_TIMEOUT"; then
    return 0
  fi

  echo -e "${YELLOW}Restarting tunnel helper once...${NC}"
  stop_tunnel_helper
  start_tunnel_helper
  .venv/bin/python scripts/wait-for-tunnel.py --timeout "$TUNNEL_TIMEOUT"
}

if ensure_tunnel; then
  echo -e "${GREEN}Tunnel is ready.${NC}\n"
else
  echo -e "${YELLOW}Tunnel is still unavailable; starting Web UI so you can inspect logs and retry.${NC}"
  echo -e "Fallback: ${YELLOW}make tunnel${NC}"
  echo -e "Logs: ${YELLOW}make tunnel-log${NC}\n"
fi

uv run uvicorn app.main:app --host 127.0.0.1 --port 8787 --reload
