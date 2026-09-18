#!/usr/bin/env bash
set -euo pipefail

# Editable installation settings
PYTHON_VERSION="3.13"
VENV_DIR=".venv"
INSTALL_SYSTEM_PACKAGES=true
PMD_VERSION="4.26.6"

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"; cd "$ROOT"
echo "Setting up GPSPOOF in $ROOT"
if $INSTALL_SYSTEM_PACKAGES && command -v apt-get >/dev/null; then
  # Install only packages whose corresponding runtime tools are actually
  # missing. In particular, do not request curl (the watchdog uses Python's
  # standard library): requesting an unnecessary curl upgrade can make setup
  # fail on Debian systems with partially synchronized backports repositories.
  required_packages=()
  command -v usbmuxd >/dev/null 2>&1 || required_packages+=(usbmuxd)
  if ((${#required_packages[@]})); then
    echo "Installing required host packages: ${required_packages[*]} (sudo may prompt)..."
    sudo apt-get update
    sudo apt-get install -y --no-install-recommends "${required_packages[@]}"
  else
    echo "Required host packages are already available; skipping apt."
  fi
fi
if command -v "python${PYTHON_VERSION}" >/dev/null; then PY="python${PYTHON_VERSION}"
elif command -v uv >/dev/null; then uv python install "$PYTHON_VERSION"; PY="$(uv python find "$PYTHON_VERSION")"
else PY=python3; echo "Warning: Python $PYTHON_VERSION unavailable; using $($PY --version)" >&2; fi
if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  if ! "$PY" -m venv "$VENV_DIR"; then
    echo "Unable to create a virtual environment." >&2
    echo "Install the venv module for $($PY --version 2>&1) (often: sudo apt install python3-venv), then rerun setup." >&2
    exit 1
  fi
fi
"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install -e '.[test]'
mkdir -p data logs runtime; chmod 700 runtime
echo "Installed pymobiledevice3: $($VENV_DIR/bin/python -c 'import importlib.metadata; print(importlib.metadata.version("pymobiledevice3"))')"
"$VENV_DIR/bin/python" - <<'PY'
import fastapi
import pymobiledevice3
import uvicorn
from pymobiledevice3.remote.rsd_tunnel import PreferredRsdTunnel
from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation

import gpspoof

print("Import and pymobiledevice3 API check: OK")
PY
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
