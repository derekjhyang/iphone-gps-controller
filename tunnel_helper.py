#!/usr/bin/env python3
"""
Start pymobiledevice3 TCP tunnel, parse the emitted --rsd HOST PORT,
and write it to .runtime/rsd.json for the Web UI/backend.

Run:
  sudo .venv/bin/python tunnel_helper.py

This process is expected to stay running.
"""
import json
import os
import re
import signal
import subprocess
import sys
import ssl
import time
from pathlib import Path

if sys.version_info < (3, 13):
    print(f"[tunnel-helper] ERROR: Python >= 3.13 required, got {sys.version.split()[0]}", file=sys.stderr)
    sys.exit(1)

if "LibreSSL" in ssl.OPENSSL_VERSION:
    print(f"[tunnel-helper] ERROR: LibreSSL is unsupported for TCP tunnel: {ssl.OPENSSL_VERSION}", file=sys.stderr)
    print("[tunnel-helper] Run: make install", file=sys.stderr)
    sys.exit(1)

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / ".runtime"
RSD_FILE = RUNTIME / "rsd.json"
LOG_FILE = RUNTIME / "tunnel.log"

RUNTIME.mkdir(exist_ok=True)


def detect_tunnel_target():
    requested = os.environ.get("TUNNEL_CONNECTION_TYPE", "auto").strip().lower()
    requested_udid = os.environ.get("TUNNEL_UDID", "").strip()

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pymobiledevice3", "usbmux", "list"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        devices = json.loads(result.stdout or "[]") if result.returncode == 0 else []
    except Exception:
        devices = []

    if requested in {"usb", "wifi"}:
        reason = f"forced by TUNNEL_CONNECTION_TYPE={requested}"
        return requested, requested_udid or None, reason

    if requested_udid:
        return "usb", requested_udid, f"forced by TUNNEL_UDID={requested_udid}; connection type fallback usb"

    connection_types = {str(device.get("ConnectionType", "")).lower() for device in devices}
    udid = None
    if devices:
        udid = devices[0].get("UniqueDeviceID") or devices[0].get("Identifier")

    if "usb" in connection_types:
        usb_device = next((device for device in devices if str(device.get("ConnectionType", "")).lower() == "usb"), devices[0])
        udid = usb_device.get("UniqueDeviceID") or usb_device.get("Identifier") or udid
        return "usb", udid, "USB device visible"
    if "network" in connection_types or "wifi" in connection_types:
        network_device = next(
            (device for device in devices if str(device.get("ConnectionType", "")).lower() in {"network", "wifi"}),
            devices[0],
        )
        udid = network_device.get("UniqueDeviceID") or network_device.get("Identifier") or udid
        return "wifi", udid, "only network device visible"
    return "usb", udid, "no devices visible; falling back to usb"


connection_type, tunnel_udid, connection_reason = detect_tunnel_target()
cmd = [
    sys.executable,
    "-m",
    "pymobiledevice3",
    "remote",
    "start-tunnel",
    "--connection-type",
    connection_type,
    "--protocol",
    "tcp",
]
if tunnel_udid:
    cmd.extend(["--udid", tunnel_udid])

with LOG_FILE.open("a", encoding="utf-8") as log:
    log.write(f"\n--- tunnel start {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
    log.write(f"connection-type: {connection_type} ({connection_reason})\n")
    if tunnel_udid:
        log.write(f"udid: {tunnel_udid}\n")
    log.write("command: " + " ".join(cmd) + "\n")
    log.flush()

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    def cleanup(signum=None, frame=None):
        try:
            if proc.poll() is None:
                proc.terminate()
        finally:
            payload = {
                "ok": False,
                "active": False,
                "stopped_at": time.time(),
                "message": "tunnel helper stopped",
            }
            RSD_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    parsed = False
    reason = None
    hint = None
    for line in proc.stdout:
        print(line, end="")
        log.write(line)
        log.flush()

        if re.search(r"requires root privileges", line, re.IGNORECASE):
            reason = "需要 macOS 管理員權限才能連接手機。"
            hint = "請用 `make run` 啟動 Web UI，或在終端機執行 `make tunnel`，讓 macOS 可以詢問你的登入密碼。"
        elif re.search(r"Device is not connected|No device|NoDevice", line, re.IGNORECASE):
            reason = "沒有偵測到可用的 iPhone 連線。"
            hint = "請確認 iPhone 已用 USB 接上、已解鎖、已信任這台 Mac，必要時重新插拔 USB 或重開 Finder 裡的信任流程後再執行 `make tunnel`。"
        elif re.search(r"Pairing|Trust|not paired|InvalidHostID", line, re.IGNORECASE):
            reason = "iPhone 尚未信任或配對這台 Mac。"
            hint = "請解鎖 iPhone，重新插拔 USB，並在手機上點選「信任這台電腦」。"

        m = re.search(r"--rsd\s+([^\s]+)\s+(\d+)", line)
        if m:
            host, port = m.group(1), int(m.group(2))
            payload = {
                "ok": True,
                "active": True,
                "host": host,
                "port": port,
                "pid": proc.pid,
                "updated_at": time.time(),
                "command": " ".join(cmd),
            }
            RSD_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            parsed = True
            print(f"[tunnel-helper] RSD saved: {host}:{port}")

    rc = proc.wait()
    payload = {
        "ok": False,
        "active": False,
        "returncode": rc,
        "message": "tunnel process exited",
        "updated_at": time.time(),
    }
    if reason:
        payload["reason"] = reason
    if hint:
        payload["hint"] = hint
    RSD_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    sys.exit(rc)
