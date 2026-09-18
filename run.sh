#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"; cd "$ROOT"
VENV_DIR="${VENV_DIR:-.venv}"; USE_WATCHDOG="${USE_WATCHDOG:-true}"; PREVENT_SLEEP="${PREVENT_SLEEP:-true}"
[[ -x "$VENV_DIR/bin/python" ]] || { echo "Missing $VENV_DIR. Run ./setup.sh first." >&2; exit 1; }
mkdir -p runtime logs
exec 9>runtime/gpspoof.lock
flock -n 9 || { echo "GPSPOOF is already running (runtime/gpspoof.lock is held)." >&2; exit 1; }
export GPSPOOF_LOCK_FD=9
if [[ "$USE_WATCHDOG" == true ]]; then cmd=("$ROOT/watchdog.sh")
else cmd=("$VENV_DIR/bin/python" "$ROOT/gpspoof.py"); fi
if [[ "$PREVENT_SLEEP" == true ]] && command -v systemd-inhibit >/dev/null; then
  exec systemd-inhibit --what=sleep:idle --who=GPSPOOF --why="Keep USB GPS simulation alive" --mode=block "${cmd[@]}"
fi
echo "Warning: systemd-inhibit unavailable; suspend will interrupt GPS persistence." >&2
exec "${cmd[@]}"
