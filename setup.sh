#!/usr/bin/env bash
set -euo pipefail

# Editable installation settings
PYTHON_VERSION="3.13"
VENV_DIR=".venv"
INSTALL_SYSTEM_PACKAGES=true
PMD_VERSION="4.18.0"

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"; cd "$ROOT"
echo "Setting up GPSPOOF in $ROOT"
if $INSTALL_SYSTEM_PACKAGES && command -v apt-get >/dev/null; then
  missing=(); for c in git curl usbmuxd; do command -v "$c" >/dev/null || missing+=("$c"); done
  if ((${#missing[@]})); then
    echo "Installing host packages (sudo may prompt)..."
    sudo apt-get update
    sudo apt-get install -y git curl usbmuxd libimobiledevice-utils build-essential python3-dev
  fi
fi
if command -v "python${PYTHON_VERSION}" >/dev/null; then PY="python${PYTHON_VERSION}"
elif command -v uv >/dev/null; then uv python install "$PYTHON_VERSION"; PY="$(uv python find "$PYTHON_VERSION")"
else PY=python3; echo "Warning: Python $PYTHON_VERSION unavailable; using $($PY --version)" >&2; fi
if [[ ! -x "$VENV_DIR/bin/python" ]]; then "$PY" -m venv "$VENV_DIR"; fi
"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install -e '.[test]'
mkdir -p data logs runtime; chmod 700 runtime
echo "Installed pymobiledevice3: $($VENV_DIR/bin/python -c 'import importlib.metadata; print(importlib.metadata.version("pymobiledevice3"))')"
"$VENV_DIR/bin/python" -c 'import fastapi, uvicorn, pymobiledevice3; import gpspoof; print("Import check: OK")'
if "$VENV_DIR/bin/pymobiledevice3" usbmux list >/dev/null 2>&1; then "$VENV_DIR/bin/pymobiledevice3" usbmux list || true
else echo "usbmux check: unavailable/no phone (safe to continue)"; fi
"$VENV_DIR/bin/python" -m pytest -q
GPSPOOF_FAKE_CHECK=1 "$VENV_DIR/bin/python" - <<'PY'
import asyncio
from models import DeviceInfo, Coordinate
from pmd_backend import PmdBackend
async def main():
    async with PmdBackend(True).session(DeviceInfo("FAKE")) as session:
        await session.location.set(1, 2); assert session.location.last == Coordinate(1, 2)
asyncio.run(main()); print("Fake backend check: OK")
PY
echo "Setup complete. Run: ./run.sh"
