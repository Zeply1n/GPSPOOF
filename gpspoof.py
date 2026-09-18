#!/usr/bin/env python3
"""GPSPOOF web application and editable operational configuration."""
# ============================================================
# GPSPOOF CONFIGURATION
# EDIT THESE CONSTANTS — DO NOT HIDE TUNABLES ELSEWHERE
# ============================================================
AUTO_TUNE = True
TARGET_UDID = None
ALLOW_CROSS_DEVICE_FALLBACK = False
WEB_HOST = "127.0.0.1"
WEB_PORT = 5000
DEFAULT_REASSERT_INTERVAL_SECONDS = 2.0
MIN_REASSERT_INTERVAL_SECONDS = 0.75
MAX_REASSERT_INTERVAL_SECONDS = 15.0
RPC_TIMEOUT_SECONDS = 8.0
RECONNECT_INITIAL_DELAY_SECONDS = 0.5
RECONNECT_MAX_DELAY_SECONDS = 10.0
RECONNECT_BACKOFF_MULTIPLIER = 1.7
RESTORE_LOCATION_AFTER_RECONNECT = True
RESTORE_LAST_LOCATION_ON_APP_RESTART = True
CLEAR_LOCATION_ON_CLEAN_EXIT = True
CLEAN_SHUTDOWN_TIMEOUT_SECONDS = 5.0
BURST_REASSERT_ON_NEW_SESSION = True
BURST_REASSERT_DELAYS_SECONDS = (0.0, 0.20, 0.60, 1.50)
ENABLE_HEARTBEAT = True  # reserved; current RSD provider owns liveness, see README
HEARTBEAT_INTERVAL_SECONDS = 15.0
ENABLE_PROACTIVE_SESSION_RECYCLING = True
PROACTIVE_SESSION_MAX_AGE_SECONDS = 300.0
FULL_SESSION_RECREATE_ON_SET_ERROR = True
ALLOW_TUNNELD_FALLBACK = False
ENABLE_LEGACY_CLI_BACKEND = False
WATCHDOG_ENABLED = True
WATCHDOG_HEALTH_TIMEOUT_SECONDS = 15.0
WATCHDOG_CHECK_INTERVAL_SECONDS = 3.0
PREVENT_SYSTEM_SLEEP_WHILE_RUNNING = True
RUN_SIMULATION_CAPABILITY_TEST = True
AUTOTUNE_REASSERT_CANDIDATES_SECONDS = (5.0, 3.0, 2.0, 1.5, 1.0, 0.75)
AUTOTUNE_FAILURES_BEFORE_RETUNE = 3
AUTOTUNE_PROFILE_FILE = "data/autotune.json"
STATE_FILE = "data/state.json"
LOCATIONS_FILE = "data/locations.json"
CAPABILITY_RESULTS_FILE = "data/capability-results.json"
LOG_FILE = "logs/gpspoof.log"
LOG_LEVEL = "INFO"
LOG_MAX_BYTES = 2_000_000
LOG_BACKUP_COUNT = 5
DEVICE_POLL_INTERVAL_SECONDS = 2.0
ROUTE_TICK_SECONDS = 1.0
MAX_UPLOAD_BYTES = 10_000_000
FAKE_DEVICE_MODE = False
DEBUG = False
# ============================================================

import asyncio
import importlib.metadata
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import platform
import sys
import time
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from autotune import AutoTuner
from device_manager import DeviceManager
from gps_engine import GpsEngine
from models import Coordinate, utcnow
from pmd_backend import PmdBackend
from route_engine import Route, parse_gpx
from state_store import AtomicJsonStore, StateStore

ROOT = Path(__file__).resolve().parent
VERSION = "1.0.0"
for directory in (ROOT/"data", ROOT/"logs"): directory.mkdir(exist_ok=True)

def resolve(path: str) -> str: return str(ROOT/path)

def pmd_version() -> str:
    try: return importlib.metadata.version("pymobiledevice3")
    except importlib.metadata.PackageNotFoundError: return "not installed"

def configure_logging() -> None:
    handler = RotatingFileHandler(resolve(LOG_FILE), maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT)
    console = logging.StreamHandler(); formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handler.setFormatter(formatter); console.setFormatter(formatter)
    logging.basicConfig(level=logging.DEBUG if DEBUG else getattr(logging, LOG_LEVEL), handlers=[handler, console])

configure_logging(); LOG = logging.getLogger("gpspoof")
CONFIG = {"restore_on_start": RESTORE_LAST_LOCATION_ON_APP_RESTART, "shutdown_timeout": CLEAN_SHUTDOWN_TIMEOUT_SECONDS,
          "reconnect_initial": RECONNECT_INITIAL_DELAY_SECONDS, "reconnect_max": RECONNECT_MAX_DELAY_SECONDS,
          "reconnect_multiplier": RECONNECT_BACKOFF_MULTIPLIER, "device_poll": DEVICE_POLL_INTERVAL_SECONDS,
          "pmd_version": pmd_version(), "burst": BURST_REASSERT_ON_NEW_SESSION, "burst_delays": BURST_REASSERT_DELAYS_SECONDS,
          "proactive_recycling": ENABLE_PROACTIVE_SESSION_RECYCLING, "watchdog_check": WATCHDOG_CHECK_INTERVAL_SECONDS,
          "watchdog_timeout": WATCHDOG_HEALTH_TIMEOUT_SECONDS, "route_tick": ROUTE_TICK_SECONDS}
devices = DeviceManager(FAKE_DEVICE_MODE, TARGET_UDID, ALLOW_CROSS_DEVICE_FALLBACK)
tuner = AutoTuner(AUTO_TUNE, resolve(AUTOTUNE_PROFILE_FILE), AUTOTUNE_REASSERT_CANDIDATES_SECONDS,
                  DEFAULT_REASSERT_INTERVAL_SECONDS, RPC_TIMEOUT_SECONDS, PROACTIVE_SESSION_MAX_AGE_SECONDS)
engine = GpsEngine(state_store=StateStore(resolve(STATE_FILE)), devices=devices, backend=PmdBackend(FAKE_DEVICE_MODE), tuner=tuner, config=CONFIG)
locations = AtomicJsonStore(resolve(LOCATIONS_FILE), {"locations": []})
capabilities = AtomicJsonStore(resolve(CAPABILITY_RESULTS_FILE), {"results": []})
APP_STARTED = time.monotonic()

@asynccontextmanager
async def lifespan(_: FastAPI):
    LOG.info("GPSPOOF %s starting with pymobiledevice3 %s", VERSION, pmd_version())
    await engine.start()
    yield
    await engine.stop(clear=CLEAR_LOCATION_ON_CLEAN_EXIT)
    LOG.info("GPSPOOF shutdown complete")

app = FastAPI(title="GPSPOOF", version=VERSION, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT/"static"), name="static")

class CoordinateRequest(BaseModel):
    latitude: float = Field(ge=-90, le=90); longitude: float = Field(ge=-180, le=180)
class DeviceRequest(BaseModel): udid: str = Field(min_length=1, max_length=128)
class NudgeRequest(BaseModel):
    direction: Literal["N","NE","E","SE","S","SW","W","NW"]
    meters: float = Field(gt=0, le=10000)
class StableRequest(BaseModel): stable: bool = True
class RoutePlayRequest(BaseModel):
    speed_mps: float = Field(default=1.4, gt=0, le=100); loop: bool = False; progress: float = Field(default=0, ge=0, le=1)
class CapabilityResult(BaseModel): result: Literal["CONFIRMED_STABLE","CONFIRMED_BUT_DECAYS","INJECTION_UNCONFIRMED","TRANSPORT_FAILURE"]
class SavedLocation(BaseModel):
    name: str = Field(min_length=1, max_length=100); latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180); notes: str = Field(default="", max_length=500)

@app.get("/", response_class=HTMLResponse)
async def index() -> FileResponse: return FileResponse(ROOT/"templates/index.html")

@app.get("/api/health")
async def health() -> dict[str, Any]:
    status = engine.status(); waiting = status["state"] in {"WAITING_FOR_DEVICE", "STOPPED"}
    wedged = status["active"] and status["last_supervisor_progress_age_seconds"] > WATCHDOG_HEALTH_TIMEOUT_SECONDS*2
    return {"web_alive": True, "engine_alive": bool(engine._main_task and not engine._main_task.done()),
            "device_unavailable": status["state"] == "WAITING_FOR_DEVICE", "session_unhealthy": status["session_health"] == "BAD",
            "healthy": not wedged and bool(engine._main_task and not engine._main_task.done()), "valid_waiting_state": waiting,
            "state": status["state"], "timestamp": utcnow()}

@app.get("/api/status")
async def status() -> dict[str, Any]: return engine.status()

@app.get("/api/devices")
async def list_devices() -> dict[str, Any]: return {"devices": [d.public() for d in await devices.devices()], "selection_required": len(await devices.devices()) > 1}

@app.post("/api/device/select")
async def select_device(body: DeviceRequest) -> dict[str, bool]:
    available = await devices.devices()
    matches = [device for device in available if body.udid in {device.udid, device.masked_udid}]
    if len(matches) != 1: raise HTTPException(409, "device selection is missing or ambiguous")
    await engine.select_device(matches[0].udid); return {"ok": True}

@app.post("/api/location/set")
async def set_location(body: CoordinateRequest) -> dict[str, bool]:
    await engine.set_location(Coordinate(body.latitude, body.longitude)); return {"ok": True}

@app.post("/api/location/clear")
async def clear_location() -> dict[str, bool]: await engine.clear(); return {"ok": True}

@app.post("/api/location/nudge")
async def nudge_location(body: NudgeRequest) -> dict[str, Any]:
    try: point = await engine.nudge(body.direction, body.meters)
    except ValueError as exc: raise HTTPException(409, str(exc)) from exc
    return {"ok": True, **point.dict()}

@app.post("/api/reconnect")
async def reconnect() -> dict[str, bool]: await engine.reconnect(); return {"ok": True}

@app.post("/api/autotune/report-bounce")
async def bounce() -> dict[str, bool]: await engine.report_bounce(); return {"ok": True}
@app.post("/api/autotune/report-stable")
async def stable() -> dict[str, bool]: engine.report_stable(); return {"ok": True}
@app.post("/api/autotune/reset")
async def reset_tuner() -> dict[str, bool]: tuner.reset(); return {"ok": True}
@app.post("/api/autotune/retune")
async def retune() -> dict[str, bool]: tuner.report_bounce(); await engine.reconnect(); return {"ok": True}

@app.post("/api/capability-test/start")
async def capability_start(body: CoordinateRequest) -> dict[str, Any]:
    if not RUN_SIMULATION_CAPABILITY_TEST: raise HTTPException(403, "capability test disabled")
    await engine.set_location(Coordinate(body.latitude, body.longitude), "capability-test"); await engine.reconnect()
    return {"ok": True, "prompt": "Observe the phone, then submit the result."}
@app.post("/api/capability-test/result")
async def capability_result(body: CapabilityResult) -> dict[str, bool]:
    data = capabilities.read(); data.setdefault("results", []).append({"timestamp": utcnow(), "result": body.result,
        "device": engine.devices.selected_udid[:4]+"…" if engine.devices.selected_udid else None, "pymobiledevice3": pmd_version()})
    capabilities.write(data)
    if body.result == "CONFIRMED_BUT_DECAYS": await engine.report_bounce()
    elif body.result == "CONFIRMED_STABLE": engine.report_stable()
    else: engine.location_confirmation = body.result
    return {"ok": True}

@app.post("/api/route/load")
async def route_load(file: Annotated[UploadFile, File()]) -> dict[str, Any]:
    data = await file.read(MAX_UPLOAD_BYTES+1)
    if len(data)>MAX_UPLOAD_BYTES: raise HTTPException(413, "GPX too large")
    try: points = parse_gpx(data)
    except Exception as exc: raise HTTPException(400, f"Invalid GPX: {exc}") from exc
    engine.route = Route(points); engine.desired.route_file = file.filename; engine.store.write(engine.desired)
    return {"ok": True, "points": len(points)}
@app.post("/api/route/play")
async def route_play(body: RoutePlayRequest) -> dict[str, bool]:
    if not engine.route: raise HTTPException(409, "load a route first")
    engine.route.speed_mps, engine.route.loop, engine.route.progress = body.speed_mps, body.loop, body.progress
    engine.route.playing, engine.route.paused = True, False
    await engine.set_location(engine.route.at(body.progress), "route"); await engine.reconnect(); return {"ok": True}
@app.post("/api/route/pause")
async def route_pause() -> dict[str, bool]:
    if engine.route: engine.route.paused = True
    return {"ok": True}
@app.post("/api/route/resume")
async def route_resume() -> dict[str, bool]:
    if engine.route: engine.route.paused = False
    return {"ok": True}
@app.post("/api/route/stop")
async def route_stop() -> dict[str, bool]:
    if engine.route: engine.route.playing = False; engine.route.paused = False
    engine.desired.mode = "fixed"; engine.store.write(engine.desired); return {"ok": True}

@app.get("/api/locations")
async def saved_locations() -> Any: return locations.read()
@app.post("/api/locations")
async def save_location(body: SavedLocation) -> dict[str, bool]:
    data=locations.read(); data.setdefault("locations", []).append(body.model_dump()); locations.write(data); return {"ok": True}

@app.get("/api/logs")
async def logs(lines: int = 100) -> dict[str, Any]:
    lines=max(1,min(lines,500)); path=Path(resolve(LOG_FILE))
    return {"lines": path.read_text(errors="replace").splitlines()[-lines:] if path.exists() else []}

def diagnostics_payload() -> dict[str, Any]:
    return {"gpspoof_version": VERSION, "python": platform.python_version(), "platform": platform.platform(),
            "pymobiledevice3": pmd_version(), "config": {"auto_tune": AUTO_TUNE, "fake_device": FAKE_DEVICE_MODE,
            "web_host": WEB_HOST, "heartbeat_requested": ENABLE_HEARTBEAT, "tunneld_fallback": ALLOW_TUNNELD_FALLBACK},
            "status": engine.status(), "generated": utcnow()}
@app.get("/api/diagnostics")
async def diagnostics(download: bool = False) -> Any:
    payload=diagnostics_payload()
    return JSONResponse(payload, headers={"Content-Disposition": "attachment; filename=gpspoof-diagnostics.json"} if download else None)

@app.get("/api/events")
async def events(request: Request) -> StreamingResponse:
    async def stream():
        while not await request.is_disconnected():
            yield f"data: {json.dumps(engine.status(), default=str)}\n\n"; await asyncio.sleep(1)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control":"no-cache"})

def banner() -> None:
    print(f"GPSPOOF\n=======\n\npymobiledevice3: {pmd_version()}\nPython: {platform.python_version()}\nAUTO_TUNE: {'enabled' if AUTO_TUNE else 'disabled'}\nWeb UI: http://{WEB_HOST}:{WEB_PORT}\nTransport preference: PreferredRsdTunnel\nWatchdog: {'enabled' if WATCHDOG_ENABLED else 'disabled'}\nSleep inhibition: {'enabled' if PREVENT_SYSTEM_SLEEP_WHILE_RUNNING else 'disabled'}\n\nWaiting for iPhone…", flush=True)
    if WEB_HOST not in {"127.0.0.1", "localhost", "::1"}: LOG.warning("SECURITY: API is exposed beyond localhost; unauthenticated users can control GPS")

if __name__ == "__main__":
    import uvicorn
    banner(); uvicorn.run(app, host=WEB_HOST, port=WEB_PORT, log_level="debug" if DEBUG else "info")
