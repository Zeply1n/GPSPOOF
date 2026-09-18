"""Async GPS desired-state engine and complete-session supervisor."""
from __future__ import annotations
import asyncio
import logging
import time
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any
from autotune import AutoTuner
from device_manager import DeviceManager
from models import Coordinate, EngineState, Metrics, PersistentState, utcnow
from pmd_backend import PmdBackend, Session
from route_engine import Route, distance, nudge
from state_store import StateStore

LOG = logging.getLogger(__name__)

class GpsEngine:
    def __init__(self, *, state_store: StateStore, devices: DeviceManager, backend: PmdBackend,
                 tuner: AutoTuner, config: dict[str, Any]) -> None:
        self.store, self.devices, self.backend, self.tuner, self.config = state_store, devices, backend, tuner, config
        self.desired: PersistentState = state_store.load()
        if not config["restore_on_start"]: self.desired.active = False
        if self.desired.selected_device_udid: self.devices.selected_udid = self.desired.selected_device_udid
        self.state, self.metrics = EngineState.STOPPED, Metrics()
        self.started_at = time.monotonic(); self.session_started: float | None = None
        self.last_progress = time.monotonic(); self.last_attempt: str | None = None
        self.last_error: str | None = None; self.location_confirmation = "UNKNOWN"
        self.rpc_health = "UNKNOWN"; self.session_health = "UNKNOWN"; self.last_bounce: str | None = None
        self._main_task: asyncio.Task[None] | None = None; self._shutdown = asyncio.Event(); self._wake = asyncio.Event()
        self._rebuild = asyncio.Event(); self._set_now = asyncio.Event(); self._set_lock = asyncio.Lock()
        self._session: Session | None = None; self.route: Route | None = None

    def transition(self, state: EngineState, detail: str | None = None) -> None:
        if self.state != state: LOG.info("State %s -> %s%s", self.state, state, f": {detail}" if detail else "")
        self.state = state; self.last_progress = time.monotonic()

    async def start(self) -> None:
        if self._main_task and not self._main_task.done(): return
        self._shutdown.clear(); self._main_task = asyncio.create_task(self._supervisor(), name="gps-supervisor")

    async def stop(self, clear: bool = True) -> None:
        if clear and self.desired.active and self._session and self._session.location:
            # A successful clean-exit clear is a deliberate clear: persist inactive so
            # a later normal start cannot unexpectedly restore it. A SIGKILL never
            # executes this path and intentionally leaves active state recoverable.
            try:
                await asyncio.wait_for(self._session.location.clear(), self.config["shutdown_timeout"])
                self.desired.active = False
                self.store.write(self.desired)
            except Exception as exc: LOG.warning("Could not clear location during shutdown: %s", exc)
        self._shutdown.set(); self._wake.set(); self._rebuild.set()
        if self._main_task:
            try: await asyncio.wait_for(self._main_task, self.config["shutdown_timeout"] + 2)
            except asyncio.TimeoutError: self._main_task.cancel()
        self.transition(EngineState.STOPPED)

    async def _supervisor(self) -> None:
        delay = self.config["reconnect_initial"]
        while not self._shutdown.is_set():
            device = await self.devices.selected()
            if not device:
                self.transition(EngineState.WAITING_FOR_DEVICE); self.session_health = "DEVICE_UNAVAILABLE"
                try: await asyncio.wait_for(self._wake.wait(), self.config["device_poll"])
                except asyncio.TimeoutError: pass
                self._wake.clear(); continue
            if self.desired.selected_device_udid != device.udid:
                self.desired.selected_device_udid = device.udid; self.store.write(self.desired)
            self.transition(EngineState.PREFLIGHT)
            check = await self.devices.preflight(device)
            if not check["ok"]:
                self.last_error = check.get("guidance"); self.transition(EngineState.ERROR, self.last_error)
                await asyncio.sleep(self.config["device_poll"]); continue
            if not self.desired.active:
                self.transition(EngineState.STOPPED); self.session_health = "IDLE"
                self._wake.clear(); await self._wake.wait(); continue
            try:
                self.transition(EngineState.CONNECTING); self._rebuild.clear()
                started = time.monotonic()
                async with self.backend.session(device) as session:
                    self._session = session; self.transition(EngineState.TUNNEL_READY)
                    self.transition(EngineState.DVT_READY); self.session_started = time.monotonic()
                    self.metrics.rsd_reconnects += 1; self.metrics.dvt_reconnects += 1
                    self.session_health = "GOOD"; delay = self.config["reconnect_initial"]
                    self.tuner.select({"udid": device.udid, "product": device.product_type, "ios": device.product_version,
                                      "build": device.build_version, "pmd": self.config["pmd_version"]})
                    await self._burst()
                    self.transition(EngineState.ROUTE_ACTIVE if self.route and self.route.playing else EngineState.SPOOF_ACTIVE)
                    tasks = [asyncio.create_task(self._reassert_loop(), name="location-reassert"),
                             asyncio.create_task(self._watchdog(), name="internal-watchdog")]
                    if self.config["proactive_recycling"]:
                        tasks.append(asyncio.create_task(self._recycle_timer(), name="session-recycle"))
                    if self.route and self.route.playing:
                        tasks.append(asyncio.create_task(self._route_loop(), name="route-playback"))
                    rebuild_task = asyncio.create_task(self._rebuild.wait(), name="rebuild-signal")
                    tasks.append(rebuild_task)
                    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for task in pending: task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    for task in done:
                        if task is not rebuild_task and not task.cancelled() and task.exception(): raise task.exception()
                self._session = None
                if self._shutdown.is_set(): break
                self.transition(EngineState.RECONNECTING)
            except asyncio.CancelledError: break
            except Exception as exc:
                self._session = None; self.last_error = f"{type(exc).__name__}: {exc}"; self.session_health = "BAD"
                LOG.exception("Session poisoned; discarding complete RSD/DVT/location hierarchy")
                self.transition(EngineState.RECONNECTING, self.last_error)
            if not self._shutdown.is_set():
                await asyncio.sleep(delay); delay = min(self.config["reconnect_max"], delay*self.config["reconnect_multiplier"])

    async def _apply(self) -> None:
        coordinate = self.desired.coordinate
        if not coordinate or not self._session or not self._session.location: return
        async with self._set_lock:
            self.last_attempt = utcnow(); started = time.monotonic()
            try:
                await asyncio.wait_for(self._session.location.set(coordinate.latitude, coordinate.longitude), self.tuner.rpc_timeout)
            except asyncio.TimeoutError:
                self.metrics.set_timeouts += 1; self.metrics.set_failures += 1; self.rpc_health = "BAD"; raise
            except Exception:
                self.metrics.set_failures += 1; self.rpc_health = "BAD"; raise
            self.metrics.set_successes += 1; self.metrics.latencies_ms.append((time.monotonic()-started)*1000)
            self.metrics.latencies_ms = self.metrics.latencies_ms[-1000:]
            self.rpc_health = "GOOD"; self.last_progress = time.monotonic(); self.desired.last_successful_set = utcnow()
            self.store.write(self.desired); LOG.debug("Location RPC accepted (confirmation remains %s)", self.location_confirmation)

    async def _burst(self) -> None:
        delays = self.config["burst_delays"] if self.config["burst"] else (0.0,)
        previous = 0.0
        for absolute_delay in delays:
            await asyncio.sleep(max(0.0, absolute_delay-previous)); previous = absolute_delay; await self._apply()

    async def _reassert_loop(self) -> None:
        while True:
            self._set_now.clear()
            try: await asyncio.wait_for(self._set_now.wait(), self.tuner.interval)
            except asyncio.TimeoutError: pass
            await self._apply()

    async def _recycle_timer(self) -> None:
        await asyncio.sleep(self.tuner.recycle_age); self.metrics.proactive_recycles += 1
        self.transition(EngineState.RECYCLING_SESSION); self._rebuild.set()

    async def _watchdog(self) -> None:
        while True:
            await asyncio.sleep(self.config["watchdog_check"])
            if self.desired.active and time.monotonic()-self.last_progress > self.config["watchdog_timeout"]:
                self.metrics.watchdog_recoveries += 1; LOG.error("Internal watchdog forcing complete session rebuild")
                self._rebuild.set(); return

    async def set_location(self, coordinate: Coordinate, mode: str = "fixed") -> None:
        self.desired.active = True; self.desired.latitude = coordinate.latitude; self.desired.longitude = coordinate.longitude
        self.desired.mode = mode; self.location_confirmation = "UNKNOWN"; self.store.write(self.desired)
        self._wake.set(); self._set_now.set(); LOG.info("Desired location changed to %.6f, %.6f", coordinate.latitude, coordinate.longitude)

    async def clear(self) -> None:
        self.transition(EngineState.CLEARING)
        # Persist inactive first: a crash can never resurrect this spoof.
        self.desired.active = False; self.desired.mode = "fixed"; self.route = None; self.store.write(self.desired)
        if self._session and self._session.location:
            try: await asyncio.wait_for(self._session.location.clear(), self.tuner.rpc_timeout)
            except Exception as exc: self.last_error = str(exc); LOG.warning("Location clear RPC failed after inactive state persisted: %s", exc)
        self._rebuild.set(); self._wake.set(); LOG.info("Location cleared; desired state is inactive")

    async def reconnect(self) -> None: self._rebuild.set(); self._wake.set()
    async def select_device(self, udid: str) -> None:
        available = {d.udid for d in await self.devices.devices()}
        if udid not in available: raise ValueError("device is not currently available")
        self.devices.select(udid); self.desired.selected_device_udid = udid; self.store.write(self.desired); await self.reconnect()
    async def nudge(self, direction: str, meters: float) -> Coordinate:
        if not self.desired.coordinate: raise ValueError("set a location before nudging")
        point = nudge(self.desired.coordinate, direction, meters); await self.set_location(point); return point
    async def report_bounce(self) -> None:
        self.metrics.bounce_reports += 1; self.last_bounce = utcnow(); self.location_confirmation = "CONFIRMED_BUT_DECAYS"
        self.transition(EngineState.SILENT_LOCATION_LAPSE); self.tuner.report_bounce(); self._set_now.set(); self._rebuild.set()
    def report_stable(self) -> None:
        self.metrics.stable_reports += 1; self.location_confirmation = "CONFIRMED_STABLE"; self.tuner.report_stable()

    async def _route_loop(self) -> None:
        assert self.route
        while self.route.playing:
            if self.route.paused: await asyncio.sleep(self.config["route_tick"]); continue
            current = self.route.at(self.route.progress); next_progress = min(1.0, self.route.progress + .001)
            nxt = self.route.at(next_progress); segment = max(distance(current, nxt), .01)
            self.route.progress = min(1.0, self.route.progress + .001*(self.route.speed_mps*self.config["route_tick"])/segment)
            point = self.route.at(self.route.progress); await self.set_location(point, "route")
            self.desired.route_progress = self.route.progress; self.store.write(self.desired)
            if self.route.progress >= 1:
                if self.route.loop: self.route.progress = 0
                else: self.route.playing = False; return
            await asyncio.sleep(self.config["route_tick"])

    def status(self) -> dict[str, Any]:
        age = time.monotonic()-self.session_started if self.session_started else None
        return {"state": self.state, "active": self.desired.active, "desired": self.desired.coordinate.dict() if self.desired.coordinate else None,
                "mode": self.desired.mode, "last_successful_set": self.desired.last_successful_set, "last_set_attempt": self.last_attempt,
                "last_error": self.last_error, "rpc_health": self.rpc_health, "session_health": self.session_health,
                "location_confirmation": self.location_confirmation, "transport": self.backend.transport_name,
                "rsd": "ready" if self._session else "disconnected", "dvt": "ready" if self._session else "disconnected",
                "session_age_seconds": age, "recycle_countdown_seconds": max(0, self.tuner.recycle_age-age) if age else None,
                "reassert_interval_seconds": self.tuner.interval, "uptime_seconds": time.monotonic()-self.started_at,
                "last_supervisor_progress_age_seconds": time.monotonic()-self.last_progress,
                "metrics": self.metrics.public(), "autotune": self.tuner.public(), "last_bounce_report": self.last_bounce,
                "selected_device": self.devices.selected_udid[:4]+"…" if self.devices.selected_udid else None,
                "route": {"progress": self.route.progress, "playing": self.route.playing, "paused": self.route.paused} if self.route else None}
