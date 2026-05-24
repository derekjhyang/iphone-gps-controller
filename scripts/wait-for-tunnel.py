#!/usr/bin/env python3
import argparse
import json
import os
import time
from pathlib import Path


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def read_pid(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def read_state(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def tail(path: Path, lines: int = 30) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except Exception:
        return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Wait until tunnel_helper writes an active RSD state.")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--state-file", default=".runtime/rsd.json")
    parser.add_argument("--pid-file", default=".runtime/tunnel.pid")
    parser.add_argument("--log-file", default=".runtime/tunnel.log")
    args = parser.parse_args()

    state_path = Path(args.state_file)
    pid_path = Path(args.pid_file)
    log_path = Path(args.log_file)
    deadline = time.monotonic() + args.timeout
    last_state = None

    while time.monotonic() < deadline:
        state = read_state(state_path)
        if state:
            last_state = state
            if state.get("ok") and state.get("active") and state.get("host") and state.get("port"):
                print(f"Tunnel ready: {state['host']}:{state['port']}")
                return 0

        pid = read_pid(pid_path)
        if pid and not pid_alive(pid):
            print(f"Tunnel helper exited early (PID {pid}).")
            break

        time.sleep(0.5)

    print("Tunnel did not become ready in time.")
    if last_state:
        reason = last_state.get("reason") or last_state.get("message")
        hint = last_state.get("hint")
        if reason:
            print(f"Reason: {reason}")
        if hint:
            print(f"Hint: {hint}")

    log_tail = tail(log_path)
    if log_tail:
        print("\nRecent tunnel log:")
        print(log_tail)

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
