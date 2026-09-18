"""Shared domain models for GPSPOOF."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


class EngineState(StrEnum):
    STOPPED = "STOPPED"
    WAITING_FOR_DEVICE = "WAITING_FOR_DEVICE"
    PREFLIGHT = "PREFLIGHT"
    CONNECTING = "CONNECTING"
    TUNNEL_READY = "TUNNEL_READY"
    DVT_READY = "DVT_READY"
    SPOOF_ACTIVE = "SPOOF_ACTIVE"
    ROUTE_ACTIVE = "ROUTE_ACTIVE"
    SIMULATION_UNCONFIRMED = "SIMULATION_UNCONFIRMED"
    SILENT_LOCATION_LAPSE = "SILENT_LOCATION_LAPSE"
    RECYCLING_SESSION = "RECYCLING_SESSION"
    RECONNECTING = "RECONNECTING"
    CLEARING = "CLEARING"
    ERROR = "ERROR"


@dataclass(slots=True)
class Coordinate:
    latitude: float
    longitude: float

    def __post_init__(self) -> None:
        if not -90 <= self.latitude <= 90:
            raise ValueError("latitude must be between -90 and 90")
        if not -180 <= self.longitude <= 180:
            raise ValueError("longitude must be between -180 and 180")

    def dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass(slots=True)
class PersistentState:
    active: bool = False
    latitude: float | None = None
    longitude: float | None = None
    mode: str = "fixed"
    selected_device_udid: str | None = None
    route_file: str | None = None
    route_progress: float = 0.0
    route_paused: bool = False
    last_successful_set: str | None = None

    @property
    def coordinate(self) -> Coordinate | None:
        if self.latitude is None or self.longitude is None:
            return None
        return Coordinate(self.latitude, self.longitude)


@dataclass(slots=True)
class DeviceInfo:
    udid: str
    name: str = "iPhone"
    product_type: str = "Unknown"
    product_version: str = "Unknown"
    build_version: str = "Unknown"
    connection_type: str = "USB"
    developer_mode: str = "unknown"
    ddi_status: str = "unknown"

    @property
    def masked_udid(self) -> str:
        return f"{self.udid[:4]}…{self.udid[-4:]}" if len(self.udid) > 10 else "••••"

    def public(self) -> dict[str, Any]:
        value = asdict(self)
        value["udid"] = self.masked_udid
        return value


@dataclass(slots=True)
class Metrics:
    set_successes: int = 0
    set_failures: int = 0
    set_timeouts: int = 0
    rsd_reconnects: int = 0
    dvt_reconnects: int = 0
    usb_disconnects: int = 0
    heartbeat_failures: int = 0
    proactive_recycles: int = 0
    watchdog_recoveries: int = 0
    bounce_reports: int = 0
    stable_reports: int = 0
    latencies_ms: list[float] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        result = asdict(self)
        values = result.pop("latencies_ms")
        result.update(average_set_latency_ms=sum(values) / len(values) if values else None,
                      minimum_set_latency_ms=min(values) if values else None,
                      maximum_set_latency_ms=max(values) if values else None)
        return result
