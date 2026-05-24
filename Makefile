.PHONY: install run tunnel doctor clean-runtime docker-up docker-down docker-logs docker-rebuild start stop

PYTHON_VERSION := 3.13

install:
	@if ! command -v uv >/dev/null 2>&1; then \
		echo "uv is required. Install with: brew install uv"; \
		exit 1; \
	fi
	uv python install $(PYTHON_VERSION)
	uv venv --python $(PYTHON_VERSION)
	uv sync
	uv pip install -U pymobiledevice3
	$(MAKE) doctor

doctor:
	.venv/bin/python scripts/doctor.py

run:
	mkdir -p .runtime
	@if pgrep -f "tunnel_helper.py" >/dev/null 2>&1; then \
		echo "Tunnel helper already running"; \
	else \
		echo "Starting tunnel helper in background..."; \
		sudo -E .venv/bin/python tunnel_helper.py > .runtime/tunnel.log 2>&1 & \
		echo $$! > .runtime/tunnel.pid; \
	fi
	uv run uvicorn app.main:app --host 127.0.0.1 --port 8787 --reload

# Keep this running on the macOS host in a second terminal.
# It writes .runtime/rsd.json for the Dockerized Web UI.
tunnel: doctor
	test -x .venv/bin/python || $(MAKE) install
	sudo -E .venv/bin/python tunnel_helper.py

# Fast alternative when the environment is already prepared.
tunnel-fast:
	test -x .venv/bin/python || (echo '.venv not found, run make install' && exit 1)
	sudo -E .venv/bin/python tunnel_helper.py

# Show current tunnel status from .runtime/rsd.json.
tunnel-status:
	cat .runtime/rsd.json 2>/dev/null || echo '.runtime/rsd.json not found or tunnel not started'

# Follow tunnel helper logs.
tunnel-log:
	tail -f .runtime/tunnel.log

docker-up:
	mkdir -p .runtime
	docker compose up --build

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f web

docker-rebuild:
	docker compose build --no-cache

clean-runtime:
	rm -f .runtime/rsd.json .runtime/tunnel.log

# Escape hatch if uv is unavailable.
install-brew-python:
	brew install python@3.13
	rm -rf .venv

# One-command startup: checks environment, starts tunnel and docker, opens browser.
start: install
	@bash scripts/run-all.sh

# Stop all services (tunnel and docker).
stop:
	@bash scripts/stop.sh

	/opt/homebrew/bin/python3.13 -m venv .venv || /usr/local/bin/python3.13 -m venv .venv
	. .venv/bin/activate && python -m pip install -U pip setuptools wheel
	. .venv/bin/activate && pip install -r requirements.txt
	. .venv/bin/activate && pip install -U pymobiledevice3
	$(MAKE) doctor
