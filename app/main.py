import os
import json
import re
import shutil
import signal
import sys
import ssl
import subprocess
import threading
import time
import math
from pathlib import Path
from dataclasses import asdict, dataclass
from typing import List, Optional

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


APP_DIR = os.path.dirname(__file__)
STATIC_DIR = os.path.join(APP_DIR, "static")
ROOT_DIR = Path(APP_DIR).resolve().parent
RUNTIME_DIR = ROOT_DIR / ".runtime"
RSD_FILE = RUNTIME_DIR / "rsd.json"
TUNNEL_LOG_FILE = RUNTIME_DIR / "tunnel.log"
TUNNEL_PID_FILE = RUNTIME_DIR / "tunnel.pid"
TUNNEL_MANAGER_LOG_FILE = RUNTIME_DIR / "tunnel-manager.log"
TUNNEL_HELPER_FILE = ROOT_DIR / "tunnel_helper.py"

app = FastAPI(title="iPhone GPS Controller", version="0.5.0-session")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@dataclass
class CommandResult:
    ok: bool
    command: str
    stdout: str
    stderr: str
    returncode: Optional[int]
    timed_out: bool = False
    reason: Optional[str] = None
    hint: Optional[str] = None


class LocationRequest(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    mode: str = Field("auto", pattern="^(auto|legacy|rsd)$")
    rsd_host: Optional[str] = None
    rsd_port: Optional[int] = Field(None, ge=1, le=65535)
    keep_session: bool = True


class RoutePoint(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)


class WalkRouteRequest(BaseModel):
    points: List[RoutePoint] = Field(..., min_length=2)
    speed_kmh: float = Field(3, gt=0, le=54)
    interval_sec: float = Field(2, ge=0.5, le=10)
    mode: str = Field("auto", pattern="^(auto|legacy|rsd)$")
    rsd_host: Optional[str] = None
    rsd_port: Optional[int] = Field(None, ge=1, le=65535)


class ActiveSession:
    process: Optional[subprocess.Popen] = None
    command: Optional[List[str]] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    started_at: Optional[float] = None
    stdout_preview: str = ""
    stderr_preview: str = ""


@dataclass
class WalkRouteState:
    active: bool = False
    stop_event: Optional[threading.Event] = None
    thread: Optional[threading.Thread] = None
    process: Optional[subprocess.Popen] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    speed_kmh: Optional[float] = None
    total_distance_m: float = 0
    traveled_m: float = 0
    segment_index: int = 0
    point_index: int = 0
    total_points: int = 0
    error: Optional[str] = None
    reason: Optional[str] = None
    command: Optional[List[str]] = None


SESSION = ActiveSession()
TUNNEL_PROCESS: Optional[subprocess.Popen] = None
ROUTE = WalkRouteState()
ROUTE_LOCK = threading.Lock()


@app.on_event("startup")
def startup_tunnel_helper():
    _start_tunnel_helper(source="startup")


@app.on_event("shutdown")
def shutdown_tunnel_helper():
    _stop_route()
    if TUNNEL_PROCESS is not None and TUNNEL_PROCESS.poll() is None:
        _stop_tunnel_helper()


def _pymd() -> List[str]:
    return [sys.executable, "-m", "pymobiledevice3"]


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_json_file(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_pid_file() -> Optional[int]:
    try:
        return int(TUNNEL_PID_FILE.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def _tunnel_process_active() -> bool:
    if TUNNEL_PROCESS is not None and TUNNEL_PROCESS.poll() is None:
        return True
    pid = _read_pid_file()
    return bool(pid and _pid_alive(pid))


def _start_tunnel_helper(source: str = "api") -> dict:
    global TUNNEL_PROCESS
    RUNTIME_DIR.mkdir(exist_ok=True)

    state = _read_json_file(RSD_FILE)
    if state and state.get("ok") and state.get("active"):
        return {
            "ok": True,
            "active": True,
            "already_running": True,
            "message": "Tunnel is already active.",
            "state": state,
        }

    if _tunnel_process_active():
        return {
            "ok": True,
            "active": False,
            "starting": True,
            "already_running": True,
            "message": "Tunnel helper is already starting.",
            "pid": _read_pid_file(),
        }

    if os.environ.get("AUTO_START_TUNNEL", "1") == "0":
        return {
            "ok": False,
            "active": False,
            "reason": "Automatic tunnel startup is disabled in this environment.",
            "hint": "請在 macOS 上使用 `make start`，或在終端機執行 `make tunnel`。",
        }

    if not TUNNEL_HELPER_FILE.exists():
        return {
            "ok": False,
            "active": False,
            "reason": "Tunnel helper is not available to this Web service.",
            "hint": "Docker 模式無法自己連接 USB 手機。請在 macOS 上使用 `make start`。",
        }

    for path in (RSD_FILE, TUNNEL_LOG_FILE):
        if path.exists() and not os.access(path, os.W_OK):
            try:
                path.unlink()
            except Exception:
                pass

    cmd = [sys.executable, str(TUNNEL_HELPER_FILE)]
    try:
        log_handle = TUNNEL_MANAGER_LOG_FILE.open("a", encoding="utf-8")
        log_handle.write(f"\n--- manager start {time.strftime('%Y-%m-%d %H:%M:%S')} ({source}) ---\n")
        log_handle.write("command: " + " ".join(cmd) + "\n")
        log_handle.flush()
        TUNNEL_PROCESS = subprocess.Popen(
            cmd,
            cwd=str(ROOT_DIR),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        TUNNEL_PID_FILE.write_text(str(TUNNEL_PROCESS.pid), encoding="utf-8")
        return {
            "ok": True,
            "active": False,
            "starting": True,
            "pid": TUNNEL_PROCESS.pid,
            "message": "Tunnel helper started. Waiting for iPhone RSD information.",
        }
    except Exception as exc:
        return {
            "ok": False,
            "active": False,
            "reason": "Failed to start tunnel helper.",
            "hint": "請在終端機執行 `make tunnel`，再回到此頁按重新檢查。",
            "error": str(exc),
        }


def _stop_tunnel_helper() -> dict:
    global TUNNEL_PROCESS
    pid = TUNNEL_PROCESS.pid if TUNNEL_PROCESS is not None else _read_pid_file()
    if not pid:
        return {"ok": True, "message": "No tunnel helper PID was recorded."}

    try:
        if _pid_alive(pid):
            os.kill(pid, signal.SIGTERM)
            time.sleep(1)
            if _pid_alive(pid):
                os.kill(pid, signal.SIGKILL)
    except Exception as exc:
        return {"ok": False, "reason": "Failed to stop tunnel helper.", "error": str(exc), "pid": pid}
    finally:
        TUNNEL_PROCESS = None
        try:
            TUNNEL_PID_FILE.unlink()
        except FileNotFoundError:
            pass

    return {"ok": True, "message": "Tunnel helper stopped.", "pid": pid}


def _run(cmd: List[str], timeout: int = 90) -> CommandResult:
    command = " ".join(cmd)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        result = CommandResult(
            ok=proc.returncode == 0,
            command=command,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            returncode=proc.returncode,
        )
    except subprocess.TimeoutExpired as exc:
        result = CommandResult(
            ok=False,
            command=command,
            stdout=exc.stdout if isinstance(exc.stdout, str) else "",
            stderr=exc.stderr if isinstance(exc.stderr, str) else "",
            returncode=None,
            timed_out=True,
            reason="Command timed out.",
            hint="For simulate-location, a long-running process can be expected. Use session mode.",
        )
    return _classify(result)


def _classify(result: CommandResult) -> CommandResult:
    text = f"{result.stdout}\n{result.stderr}"

    patterns = [
        (
            r"Invalid value for '--rsd'",
            "Invalid --rsd usage.",
            "--rsd requires HOST PORT in this pymobiledevice3 version.",
        ),
        (
            r"Unable to connect to Tunneld",
            "Unable to connect to tunneld.",
            "Run: sudo python -m pymobiledevice3 remote start-tunnel --protocol tcp",
        ),
        (
            r"Make sure you passed the --rsd option",
            "RSD tunnel is required for iOS 17+.",
            "Start tunnel and use RSD mode with HOST and PORT.",
        ),
        (
            r"enable-developer-mode",
            "Developer Mode is not enabled.",
            "Enable Developer Mode manually on iPhone, reboot, unlock, and retry.",
        ),
        (
            r"DeveloperDiskImage|PersonalizedImage|mounter auto-mount",
            "Developer image is not mounted.",
            "Run Auto Mount Image after tunnel is available.",
        ),
        (
            r"InvalidServiceError|Failed to start service",
            "Developer service failed.",
            "Check Developer Mode, mounted image, tunnel, and iOS compatibility.",
        ),
        (
            r"No device|NoDevice|Could not connect|Connection refused",
            "No usable iPhone connection.",
            "Unlock iPhone, trust this computer, reconnect USB, then retry.",
        ),
    ]

    for pattern, reason, hint in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            result.ok = False
            result.reason = reason
            result.hint = hint
            return result

    return result


def _build_set_candidates(req: LocationRequest) -> List[List[str]]:
    candidates = []
    if req.mode in ("rsd", "auto"):
        if req.rsd_host and req.rsd_port:
            candidates.append(
                _pymd()
                + [
                    "developer",
                    "dvt",
                    "simulate-location",
                    "set",
                    "--rsd",
                    req.rsd_host,
                    str(req.rsd_port),
                    "--",
                    str(req.lat),
                    str(req.lon),
                ]
            )
        elif req.mode == "rsd":
            return []

    if req.mode in ("legacy", "auto"):
        candidates.append(
            _pymd()
            + [
                "developer",
                "dvt",
                "simulate-location",
                "set",
                "--",
                str(req.lat),
                str(req.lon),
            ]
        )
    return candidates


def _distance_m(a: RoutePoint, b: RoutePoint) -> float:
    radius_m = 6371000
    lat1 = math.radians(a.lat)
    lat2 = math.radians(b.lat)
    dlat = math.radians(b.lat - a.lat)
    dlon = math.radians(b.lon - a.lon)
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * radius_m * math.asin(math.sqrt(h))


def _interpolate(a: RoutePoint, b: RoutePoint, fraction: float) -> RoutePoint:
    fraction = max(0, min(1, fraction))
    return RoutePoint(
        lat=a.lat + (b.lat - a.lat) * fraction,
        lon=a.lon + (b.lon - a.lon) * fraction,
    )


def _route_total_distance(points: List[RoutePoint]) -> float:
    return sum(_distance_m(points[i], points[i + 1]) for i in range(len(points) - 1))


def _terminate_process(proc: Optional[subprocess.Popen]) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=3)


def _start_location_process(cmd: List[str], lat: float, lon: float) -> subprocess.Popen:
    global SESSION
    _terminate_process(SESSION.process)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    SESSION.process = proc
    SESSION.command = cmd
    SESSION.lat = lat
    SESSION.lon = lon
    SESSION.started_at = time.time()
    SESSION.stdout_preview = ""
    SESSION.stderr_preview = ""
    return proc


def _stop_active_session() -> dict:
    if SESSION.process is None:
        return {"ok": True, "message": "No active simulate-location session."}

    proc = SESSION.process
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

    payload = {
        "ok": True,
        "message": "Stopped active simulate-location session.",
        "pid": proc.pid,
        "returncode": proc.returncode,
        "command": " ".join(SESSION.command or []),
    }

    SESSION.process = None
    SESSION.command = None
    SESSION.lat = None
    SESSION.lon = None
    SESSION.started_at = None
    SESSION.stdout_preview = ""
    SESSION.stderr_preview = ""
    return payload


def _stop_route(keep_final_location: bool = False) -> dict:
    global ROUTE
    with ROUTE_LOCK:
        event = ROUTE.stop_event
        thread = ROUTE.thread
        proc = ROUTE.process
        was_active = ROUTE.active
        if event:
            event.set()

    if thread and thread.is_alive():
        thread.join(timeout=6)

    with ROUTE_LOCK:
        proc = ROUTE.process or proc

    if not keep_final_location:
        _terminate_process(proc)
        if SESSION.process is proc:
            SESSION.process = None
            SESSION.command = None
            SESSION.lat = None
            SESSION.lon = None
            SESSION.started_at = None

    with ROUTE_LOCK:
        ROUTE.active = False
        ROUTE.stop_event = None
        ROUTE.thread = None
        ROUTE.process = None if not keep_final_location else ROUTE.process
        ROUTE.finished_at = time.time()
        ROUTE.reason = "stopped" if was_active else ROUTE.reason

    return {"ok": True, "active": False, "message": "Walk route stopped."}


def _run_walk_route(req: WalkRouteRequest, stop_event: threading.Event) -> None:
    global ROUTE
    points = req.points
    traveled_before_segment = 0.0
    speed_mps = req.speed_kmh / 3.6
    try:
        for segment_index in range(len(points) - 1):
            start = points[segment_index]
            end = points[segment_index + 1]
            segment_distance = _distance_m(start, end)
            duration = segment_distance / speed_mps if segment_distance > 0 else 0
            steps = max(1, math.ceil(duration / req.interval_sec))

            for step in range(steps + 1):
                if stop_event.is_set():
                    return
                fraction = step / steps
                current = _interpolate(start, end, fraction)
                location_req = LocationRequest(
                    lat=current.lat,
                    lon=current.lon,
                    mode=req.mode,
                    rsd_host=req.rsd_host,
                    rsd_port=req.rsd_port,
                    keep_session=True,
                )
                candidates = _build_set_candidates(location_req)
                if not candidates:
                    raise RuntimeError("Missing RSD host/port for route simulation.")

                cmd = candidates[0]
                update_started = time.monotonic()
                proc = _start_location_process(cmd, current.lat, current.lon)
                time.sleep(0.8)
                if proc.poll() is not None:
                    out, err = proc.communicate(timeout=3)
                    result = _classify(
                        CommandResult(
                            ok=proc.returncode == 0,
                            command=" ".join(cmd),
                            stdout=out or "",
                            stderr=err or "",
                            returncode=proc.returncode,
                        )
                    )
                    if not result.ok:
                        raise RuntimeError(result.reason or result.stderr or result.stdout or "Location update failed.")

                with ROUTE_LOCK:
                    ROUTE.process = proc
                    ROUTE.command = cmd
                    ROUTE.lat = current.lat
                    ROUTE.lon = current.lon
                    ROUTE.segment_index = segment_index
                    ROUTE.point_index = segment_index + fraction
                    ROUTE.traveled_m = min(
                        ROUTE.total_distance_m,
                        traveled_before_segment + (segment_distance * fraction),
                    )

                deadline = update_started + req.interval_sec
                while time.monotonic() < deadline:
                    if stop_event.is_set():
                        return
                    time.sleep(0.1)

            traveled_before_segment += segment_distance

        with ROUTE_LOCK:
            ROUTE.active = False
            ROUTE.finished_at = time.time()
            ROUTE.reason = "completed"
            ROUTE.traveled_m = ROUTE.total_distance_m
    except Exception as exc:
        with ROUTE_LOCK:
            ROUTE.active = False
            ROUTE.finished_at = time.time()
            ROUTE.error = str(exc)
            ROUTE.reason = "failed"


@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(STATIC_DIR, "index.html"), "r", encoding="utf-8") as f:
        return f.read()


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "python": shutil.which("python3") or shutil.which("python"),
        "pymobiledevice3": shutil.which("pymobiledevice3"),
        "version": "0.6.0-uv-py313",
        "python_version": sys.version.split()[0],
        "openssl": ssl.OPENSSL_VERSION,
        "runtime_ok": sys.version_info >= (3, 13) and "LibreSSL" not in ssl.OPENSSL_VERSION,
    }


@app.get("/api/devices")
def devices():
    result = _run(_pymd() + ["usbmux", "list"], timeout=20)
    payload = asdict(result)
    payload["raw"] = result.stdout or result.stderr
    try:
        device_list = json.loads(result.stdout or "[]") if result.ok else []
    except json.JSONDecodeError:
        device_list = []
    connection_types = sorted({
        str(device.get("ConnectionType", "")).strip()
        for device in device_list
        if device.get("ConnectionType")
    })
    payload["devices"] = device_list
    payload["device_count"] = len(device_list)
    payload["connection_types"] = connection_types
    payload["usb_connected"] = any(item.lower() == "usb" for item in connection_types)
    payload["network_visible"] = any(item.lower() in {"network", "wifi"} for item in connection_types)
    return payload



@app.get("/api/tunnel")
def tunnel_status():
    helper_pid = _read_pid_file()
    helper_active = bool(helper_pid and _pid_alive(helper_pid))
    if not RSD_FILE.exists():
        return {
            "ok": False,
            "active": False,
            "helper_active": helper_active,
            "helper_pid": helper_pid,
            "reason": "Tunnel is not running or RSD file does not exist.",
            "hint": "系統會自動嘗試連接手機。若一直無法連線，請在終端機執行 `make tunnel`。",
        }

    try:
        payload = json.loads(RSD_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "ok": False,
            "active": False,
            "helper_active": helper_active,
            "helper_pid": helper_pid,
            "reason": "Failed to read tunnel state.",
            "error": str(exc),
        }

    payload.setdefault("helper_active", helper_active)
    payload.setdefault("helper_pid", helper_pid)
    return payload


@app.post("/api/tunnel/start")
def start_tunnel():
    return _start_tunnel_helper(source="api")


@app.post("/api/tunnel/stop")
def stop_tunnel():
    return _stop_tunnel_helper()


@app.get("/api/tunnel/log")
def tunnel_log():
    if not TUNNEL_LOG_FILE.exists():
        return {"ok": False, "log": "", "reason": "No tunnel log yet."}
    text = TUNNEL_LOG_FILE.read_text(encoding="utf-8", errors="replace")
    return {"ok": True, "log": text[-8000:]}


@app.get("/api/help/simulate-location")
def simulate_location_help():
    result = _run(_pymd() + ["developer", "dvt", "simulate-location", "set", "-h"], timeout=20)
    payload = asdict(result)
    payload["rsd_requires_host_port"] = "--rsd HOST PORT" in (result.stdout + result.stderr)
    return payload


@app.post("/api/setup/enable-developer-mode")
def enable_developer_mode():
    return asdict(_run(_pymd() + ["amfi", "enable-developer-mode"], timeout=120))


@app.post("/api/setup/mount")
def mount_developer_image(rsd_host: Optional[str] = None, rsd_port: Optional[int] = None):
    cmd = _pymd() + ["mounter", "auto-mount"]
    if rsd_host and rsd_port:
        cmd += ["--rsd", rsd_host, str(rsd_port)]
    result = _run(cmd, timeout=180)
    payload = asdict(result)
    # "already mounted" is operationally success.
    if "DeveloperDiskImage already mounted" in (result.stderr or result.stdout):
        payload["ok"] = True
        payload["reason"] = None
        payload["hint"] = "DeveloperDiskImage is already mounted."
    return payload


@app.post("/api/location/set")
def set_location(req: LocationRequest):
    candidates = _build_set_candidates(req)
    if not candidates:
        return {
            "ok": False,
            "reason": "Missing RSD host/port.",
            "hint": "Run `sudo .venv/bin/python -m pymobiledevice3 remote start-tunnel --protocol tcp`, then copy HOST and PORT into the UI.",
            "lat": req.lat,
            "lon": req.lon,
        }

    # Session mode is correct for pymobiledevice3 because simulate-location set may stay alive:
    # "Press Ctrl+C to send a SIGINT..."
    if req.keep_session:
        _stop_active_session()
        cmd = candidates[0]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(2)

        stdout_preview = ""
        stderr_preview = ""

        # If process exited early, capture logs and classify as failure/success.
        if proc.poll() is not None:
            out, err = proc.communicate(timeout=5)
            result = _classify(
                CommandResult(
                    ok=proc.returncode == 0,
                    command=" ".join(cmd),
                    stdout=out or "",
                    stderr=err or "",
                    returncode=proc.returncode,
                )
            )
            payload = asdict(result)
            payload.update({"lat": req.lat, "lon": req.lon, "mode": req.mode, "session_started": False})
            return payload

        SESSION.process = proc
        SESSION.command = cmd
        SESSION.lat = req.lat
        SESSION.lon = req.lon
        SESSION.started_at = time.time()
        SESSION.stdout_preview = stdout_preview
        SESSION.stderr_preview = stderr_preview

        return {
            "ok": True,
            "session_started": True,
            "pid": proc.pid,
            "command": " ".join(cmd),
            "lat": req.lat,
            "lon": req.lon,
            "mode": req.mode,
            "hint": "simulate-location is running as a background session. Use Stop Session or Clear Location when done.",
        }

    # Non-session fallback.
    attempts = []
    for cmd in candidates:
        result = _run(cmd, timeout=8)
        attempts.append(asdict(result))
        if result.ok or result.timed_out:
            payload = asdict(result)
            payload.update({"lat": req.lat, "lon": req.lon, "mode": req.mode, "attempts": attempts})
            return payload

    final = attempts[-1]
    final.update({"lat": req.lat, "lon": req.lon, "mode": req.mode, "attempts": attempts})
    return final


@app.get("/api/location/session")
def session_status():
    with ROUTE_LOCK:
        route_active = ROUTE.active

    if SESSION.process is None:
        return {"ok": True, "active": False, "route_active": route_active}

    active = SESSION.process.poll() is None
    return {
        "ok": True,
        "active": active or route_active,
        "route_active": route_active,
        "pid": SESSION.process.pid,
        "returncode": SESSION.process.returncode,
        "command": " ".join(SESSION.command or []),
        "lat": SESSION.lat,
        "lon": SESSION.lon,
        "started_at": SESSION.started_at,
    }


@app.get("/api/location/current")
def current_location():
    with ROUTE_LOCK:
        if ROUTE.lat is not None and ROUTE.lon is not None and (ROUTE.active or ROUTE.reason in ("running", "completed")):
            return {
                "ok": True,
                "lat": ROUTE.lat,
                "lon": ROUTE.lon,
                "source": "walking_route",
                "active": ROUTE.active,
                "progress": ROUTE.traveled_m / ROUTE.total_distance_m if ROUTE.total_distance_m else 0,
                "message": "Current simulated walking-route location.",
            }

    if SESSION.lat is not None and SESSION.lon is not None:
        active = SESSION.process is not None and SESSION.process.poll() is None
        return {
            "ok": True,
            "lat": SESSION.lat,
            "lon": SESSION.lon,
            "source": "simulated_location",
            "active": active,
            "message": "Current location last set by this app.",
        }

    return {
        "ok": False,
        "reason": "Cannot read the iPhone's physical GPS location through pymobiledevice3.",
        "hint": "This app can show the current simulated location after you set a location or start a walking route.",
    }


@app.post("/api/location/session/stop")
def stop_session():
    _stop_route()
    return _stop_active_session()


@app.post("/api/location/route/start")
def start_walk_route(req: WalkRouteRequest):
    global ROUTE
    candidates = _build_set_candidates(
        LocationRequest(
            lat=req.points[0].lat,
            lon=req.points[0].lon,
            mode=req.mode,
            rsd_host=req.rsd_host,
            rsd_port=req.rsd_port,
            keep_session=True,
        )
    )
    if not candidates:
        return {
            "ok": False,
            "reason": "Missing RSD host/port.",
            "hint": "Start the phone connection first, then start the walking route.",
        }

    _stop_route()
    _stop_active_session()

    total_distance = _route_total_distance(req.points)
    stop_event = threading.Event()
    thread = threading.Thread(target=_run_walk_route, args=(req, stop_event), daemon=True)
    with ROUTE_LOCK:
        ROUTE = WalkRouteState(
            active=True,
            stop_event=stop_event,
            thread=thread,
            started_at=time.time(),
            finished_at=None,
            lat=req.points[0].lat,
            lon=req.points[0].lon,
            speed_kmh=req.speed_kmh,
            total_distance_m=total_distance,
            traveled_m=0,
            segment_index=0,
            point_index=0,
            total_points=len(req.points),
            error=None,
            reason="running",
        )

    thread.start()
    return {
        "ok": True,
        "active": True,
        "message": "Walk route started.",
        "total_distance_m": total_distance,
        "estimated_seconds": total_distance / (req.speed_kmh / 3.6) if req.speed_kmh else None,
        "speed_kmh": req.speed_kmh,
        "points": len(req.points),
    }


@app.post("/api/location/route/stop")
def stop_walk_route():
    return _stop_route()


@app.get("/api/location/route")
def walk_route_status():
    with ROUTE_LOCK:
        payload = {
            "active": ROUTE.active,
            "started_at": ROUTE.started_at,
            "finished_at": ROUTE.finished_at,
            "lat": ROUTE.lat,
            "lon": ROUTE.lon,
            "speed_kmh": ROUTE.speed_kmh,
            "total_distance_m": ROUTE.total_distance_m,
            "traveled_m": ROUTE.traveled_m,
            "segment_index": ROUTE.segment_index,
            "point_index": ROUTE.point_index,
            "total_points": ROUTE.total_points,
            "error": ROUTE.error,
            "reason": ROUTE.reason,
            "progress": ROUTE.traveled_m / ROUTE.total_distance_m if ROUTE.total_distance_m else 0,
            "command": " ".join(ROUTE.command or []) if ROUTE.command else None,
        }
        if ROUTE.process is not None:
            payload["pid"] = ROUTE.process.pid
            payload["returncode"] = ROUTE.process.returncode
    payload["ok"] = payload.get("error") is None
    return payload


@app.post("/api/location/clear")
def clear_location(mode: str = "auto", rsd_host: Optional[str] = None, rsd_port: Optional[int] = None):
    _stop_route()
    stop_payload = _stop_active_session()

    candidates = []
    if mode in ("rsd", "auto") and rsd_host and rsd_port:
        candidates.append(_pymd() + ["developer", "dvt", "simulate-location", "clear", "--rsd", rsd_host, str(rsd_port)])
    if mode in ("legacy", "auto"):
        candidates.append(_pymd() + ["developer", "dvt", "simulate-location", "clear"])

    attempts = []
    for cmd in candidates:
        result = _run(cmd, timeout=20)
        attempts.append(asdict(result))
        if result.ok or result.timed_out:
            payload = asdict(result)
            payload["attempts"] = attempts
            payload["stopped_session"] = stop_payload
            return payload

    if attempts:
        final = attempts[-1]
    else:
        final = {"ok": True, "reason": None, "hint": "Stopped session only; no clear command generated."}
    final["attempts"] = attempts
    final["stopped_session"] = stop_payload
    return final
