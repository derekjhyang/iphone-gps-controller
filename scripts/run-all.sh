#!/bin/bash
set -e

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}=== iPhone GPS Controller Startup ===${NC}\n"

# Create runtime directory
mkdir -p .runtime

# Check if tunnel is already running
if pgrep -f "tunnel_helper.py" > /dev/null; then
    echo -e "${GREEN}✓ Tunnel helper already running${NC}"
else
    echo -e "${YELLOW}Starting tunnel helper...${NC}"
    # Start tunnel in background with output redirection
    nohup sudo -E .venv/bin/python tunnel_helper.py > .runtime/tunnel.log 2>&1 &
    TUNNEL_PID=$!
    echo $TUNNEL_PID > .runtime/tunnel.pid
    echo -e "${GREEN}✓ Tunnel started (PID: $TUNNEL_PID)${NC}"
    sleep 2
fi

# Check if docker container is already running
if docker ps --format '{{.Names}}' | grep -q '^iphone-gps-controller-web$'; then
    echo -e "${GREEN}✓ Docker service already running${NC}"
else
    echo -e "${YELLOW}Starting Docker service...${NC}"
    docker compose up -d --build > /dev/null 2>&1
    echo -e "${GREEN}✓ Docker service started${NC}"
    sleep 3
fi

# Check if service is ready by querying /api/devices
RETRY=0
MAX_RETRIES=15
while [ $RETRY -lt $MAX_RETRIES ]; do
    if curl -s http://localhost:8787/api/devices > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Service is ready${NC}\n"
        break
    fi
    RETRY=$((RETRY + 1))
    if [ $RETRY -lt $MAX_RETRIES ]; then
        echo -e "${YELLOW}Waiting for service... ($RETRY/$MAX_RETRIES)${NC}"
        sleep 1
    fi
done

if [ $RETRY -eq $MAX_RETRIES ]; then
    echo -e "${YELLOW}Service may still be starting. Opening browser anyway...${NC}\n"
fi

# Open browser
echo -e "${BLUE}Opening http://localhost:8787...${NC}"
sleep 1
open http://localhost:8787

echo -e "\n${GREEN}Service is running!${NC}"
echo -e "To stop: ${YELLOW}make stop${NC}"
echo -e "Logs: ${YELLOW}make tunnel-log${NC} or ${YELLOW}make docker-logs${NC}\n"
