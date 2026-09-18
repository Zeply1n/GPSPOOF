"""USB device discovery and preflight using pymobiledevice3 public APIs."""
from __future__ import annotations
import asyncio
import logging
from typing import Any
from models import DeviceInfo

LOG = logging.getLogger(__name__)

class DeviceManager:
    def __init__(self, fake: bool, target_udid: str | None, allow_cross_device: bool = False) -> None:
        self.fake, self.selected_udid, self.allow_cross_device = fake, target_udid, allow_cross_device

    async def devices(self) -> list[DeviceInfo]:
        if self.fake:
            return [DeviceInfo("FAKE-UDID-00000001", "GPSPOOF Test iPhone", "iPhone14,7", "26.5", "FAKE", "USB", "enabled", "available")]
        return await asyncio.to_thread(self._discover)

    def _discover(self) -> list[DeviceInfo]:
        try:
            from pymobiledevice3.usbmux import list_devices
            result = []
            for mux in list_devices():
                udid = str(getattr(mux, "serial", getattr(mux, "udid", "")))
                connection = str(getattr(mux, "connection_type", "USB"))
                info = DeviceInfo(udid=udid, connection_type=connection)
                try:
                    from pymobiledevice3.lockdown import create_using_usbmux
                    lockdown = create_using_usbmux(serial=udid)
                    values = getattr(lockdown, "all_values", {}) or {}
                    info.name = values.get("DeviceName", info.name)
                    info.product_type = values.get("ProductType", info.product_type)
                    info.product_version = values.get("ProductVersion", info.product_version)
                    info.build_version = values.get("BuildVersion", info.build_version)
                    info.developer_mode = str(values.get("DeveloperModeStatus", "unknown")).lower()
                except Exception as exc:
                    LOG.warning("Lockdown preflight failed for %s: %s", udid[:6], exc)
                result.append(info)
            return result
        except Exception as exc:
            LOG.warning("USB discovery failed (is usbmuxd running?): %s", exc)
            return []

    async def selected(self) -> DeviceInfo | None:
        devices = await self.devices()
        if self.selected_udid:
            return next((d for d in devices if d.udid == self.selected_udid), None)
        if len(devices) == 1:
            self.selected_udid = devices[0].udid
            return devices[0]
        return None

    def select(self, udid: str) -> None: self.selected_udid = udid

    async def preflight(self, device: DeviceInfo) -> dict[str, Any]:
        return {"ok": True, "device": device.public()} if self.fake else {
            "ok": device.product_version != "Unknown",
            "device": device.public(),
            "guidance": None if device.product_version != "Unknown" else "Unlock and trust the phone; try `pymobiledevice3 lockdown pair`."
        }
