"""Conservative, user-feedback-driven persistence tuning."""
from __future__ import annotations
import platform
from typing import Any
from state_store import AtomicJsonStore

class AutoTuner:
    def __init__(self, enabled: bool, path: str, candidates: tuple[float, ...], manual_interval: float,
                 manual_rpc_timeout: float, manual_recycle_age: float) -> None:
        self.enabled, self.store, self.candidates = enabled, AtomicJsonStore(path, {"profiles": {}}), candidates
        self.manual = {"reassert_interval": manual_interval, "rpc_timeout": manual_rpc_timeout,
                       "recycle_age": manual_recycle_age, "heartbeat": True}
        self.key = "unselected"; self.profile = dict(self.manual, confidence=0.0, bounces=0, stable=0)

    def select(self, identity: dict[str, str]) -> None:
        self.key = "|".join((identity.get("udid", "unknown"), identity.get("product", "unknown"),
                             identity.get("ios", "unknown"), identity.get("build", "unknown"),
                             identity.get("pmd", "unknown"), platform.platform()))
        if self.enabled: self.profile.update(self.store.read().get("profiles", {}).get(self.key, {}))

    @property
    def interval(self) -> float: return float(self.profile["reassert_interval"] if self.enabled else self.manual["reassert_interval"])
    @property
    def rpc_timeout(self) -> float: return float(self.profile["rpc_timeout"] if self.enabled else self.manual["rpc_timeout"])
    @property
    def recycle_age(self) -> float: return float(self.profile["recycle_age"] if self.enabled else self.manual["recycle_age"])

    def _save(self) -> None:
        if not self.enabled: return
        data = self.store.read(); data.setdefault("profiles", {})[self.key] = self.profile; self.store.write(data)

    def report_bounce(self) -> None:
        if not self.enabled: return
        current = self.interval
        aggressive = sorted(self.candidates, reverse=True)
        try: self.profile["reassert_interval"] = aggressive[min(aggressive.index(current) + 1, len(aggressive)-1)]
        except ValueError: self.profile["reassert_interval"] = min(self.candidates, key=lambda x: abs(x-current))
        self.profile["recycle_age"] = max(30.0, self.recycle_age * .75)
        self.profile["bounces"] += 1; self.profile["confidence"] = max(0.0, self.profile["confidence"] - .2); self._save()

    def report_stable(self) -> None:
        if not self.enabled: return
        self.profile["stable"] += 1; self.profile["confidence"] = min(1.0, self.profile["confidence"] + .1); self._save()

    def reset(self) -> None:
        self.profile = dict(self.manual, confidence=0.0, bounces=0, stable=0); self._save()

    def public(self) -> dict[str, Any]: return {"enabled": self.enabled, "key": self.key, **self.profile}
