#!/usr/bin/env python3
import platform
import json
import ssl
import subprocess
import sys


def fail(msg: str) -> None:
    print(f"❌ {msg}", file=sys.stderr)
    sys.exit(1)


def ok(msg: str) -> None:
    print(f"✅ {msg}")


if sys.version_info < (3, 13):
    fail(f"Python >= 3.13 required, got {sys.version.split()[0]}")
ok(f"Python {sys.version.split()[0]}")

openssl = ssl.OPENSSL_VERSION
if "LibreSSL" in openssl:
    fail(f"Unsupported SSL runtime: {openssl}. Use uv Python 3.13 / OpenSSL 3.")
if "OpenSSL" not in openssl:
    fail(f"Unexpected SSL runtime: {openssl}")
ok(openssl)

try:
    import pymobiledevice3  # noqa: F401
except Exception as exc:
    fail(f"pymobiledevice3 import failed: {exc}")
ok("pymobiledevice3 import OK")

try:
    out = subprocess.run(
        [sys.executable, "-m", "pymobiledevice3", "version"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    version_text = (out.stdout or out.stderr or "").strip()
    ok(f"pymobiledevice3 CLI OK {version_text}")
except Exception as exc:
    fail(f"pymobiledevice3 CLI failed: {exc}")

try:
    out = subprocess.run(
        [sys.executable, "-m", "pymobiledevice3", "usbmux", "list"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    devices_text = (out.stdout or out.stderr or "").strip()
    devices = json.loads(out.stdout or "[]") if out.returncode == 0 else []
    if devices:
        connection_types = ", ".join(sorted({device.get("ConnectionType", "Unknown") for device in devices}))
        ok(f"iPhone visible via {connection_types}")
        if "USB" not in {device.get("ConnectionType") for device in devices}:
            print("⚠️  USB connection is not visible; tunnel helper will try Wi-Fi tunnel")
    else:
        print("⚠️  iPhone USB connection not visible yet")
        print("   Unlock iPhone, trust this Mac, reconnect USB, then run: make tunnel")
except Exception as exc:
    print(f"⚠️  iPhone USB connection check skipped: {exc}")

ok("runtime doctor passed")
