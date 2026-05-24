#!/bin/bash
set -e

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}=== Stopping iPhone GPS Controller ===${NC}\n"

# Stop tunnel helper
if [ -f .runtime/tunnel.pid ]; then
    PID=$(cat .runtime/tunnel.pid)
    if kill -0 $PID 2>/dev/null; then
        echo -e "${YELLOW}Stopping tunnel helper (PID: $PID)...${NC}"
        kill -TERM $PID 2>/dev/null || true
        sleep 1
        kill -9 $PID 2>/dev/null || true
        echo -e "${GREEN}✓ Tunnel stopped${NC}"
    fi
    rm -f .runtime/tunnel.pid
else
    # Try to kill any tunnel_helper processes
    if pgrep -f "tunnel_helper.py" > /dev/null; then
        echo -e "${YELLOW}Stopping tunnel helper processes...${NC}"
        pkill -f "tunnel_helper.py" || true
        sleep 1
        pkill -9 -f "tunnel_helper.py" || true
        echo -e "${GREEN}✓ Tunnel stopped${NC}"
    fi
fi

# Stop docker
echo -e "${YELLOW}Stopping Docker service...${NC}"
docker compose down > /dev/null 2>&1
echo -e "${GREEN}✓ Docker service stopped${NC}\n"

echo -e "${GREEN}All services stopped.${NC}"
echo -e "To start again: ${YELLOW}make start${NC}\n"
