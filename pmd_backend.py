"""Persistent pymobiledevice3 RSD/DVT transport.

Imports are intentionally lazy so fake mode and the web UI can run before setup.
The pinned release exposes all three objects as async context managers.
"""
from __future__ import annotations
import asyncio
import logging
from contextlib import AsyncExitStack
from typing import Any
from models import Coordinate, DeviceInfo

LOG = logging.getLogger(__name__)

class FakeLocation:
    def __init__(self) -> None: self.last: Coordinate | None = None; self.cleared = False
    async def set(self, latitude: float, longitude: float) -> None:
        await asyncio.sleep(0.005); self.last = Coordinate(latitude, longitude); self.cleared = False
    async def clear(self) -> None: self.last = None; self.cleared = True

class Session:
    def __init__(self, device: DeviceInfo, fake: bool = False) -> None:
        self.device, self.fake, self.stack, self.rsd, self.dvt, self.location = device, fake, AsyncExitStack(), None, None, None

    async def __aenter__(self) -> "Session":
        await self.stack.__aenter__()
        if self.fake:
            self.rsd = object(); self.dvt = object(); self.location = FakeLocation(); return self
        from pymobiledevice3.remote.rsd_tunnel import PreferredRsdTunnel
        from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
        from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation
        # 4.26.6: PreferredRsdTunnel(serial=...) selects userspace on Linux.
        self.rsd = await self.stack.enter_async_context(PreferredRsdTunnel(serial=self.device.udid))
        self.dvt = await self.stack.enter_async_context(DvtProvider(self.rsd))
        self.location = await self.stack.enter_async_context(LocationSimulation(self.dvt))
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.stack.__aexit__(exc_type, exc, tb)

class PmdBackend:
    transport_name = "PreferredRsdTunnel"
    def __init__(self, fake: bool = False) -> None: self.fake = fake
    def session(self, device: DeviceInfo) -> Session: return Session(device, self.fake)
