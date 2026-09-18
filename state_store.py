"""Crash-safe JSON persistence."""
from __future__ import annotations
import json
import os
import tempfile
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any, TypeVar
from models import PersistentState

T = TypeVar("T")

class AtomicJsonStore:
    def __init__(self, path: str | Path, default: Any) -> None:
        self.path = Path(path)
        self.default = default
        self._lock = threading.Lock()

    def read(self) -> Any:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return self.default.copy() if isinstance(self.default, dict) else self.default

    def write(self, value: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(value) if hasattr(value, "__dataclass_fields__") else value
        with self._lock:
            fd, name = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, indent=2, sort_keys=True)
                    handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
                os.replace(name, self.path)
                directory = os.open(self.path.parent, os.O_RDONLY)
                try: os.fsync(directory)
                finally: os.close(directory)
            finally:
                if os.path.exists(name): os.unlink(name)

class StateStore(AtomicJsonStore):
    def __init__(self, path: str | Path) -> None: super().__init__(path, {})
    def load(self) -> PersistentState:
        raw = self.read()
        allowed = PersistentState.__dataclass_fields__.keys()
        return PersistentState(**{k: v for k, v in raw.items() if k in allowed})
