#!/usr/bin/env bash
set -uo pipefail
HEALTH_URL="http://127.0.0.1:5000/api/health"
CHECK_INTERVAL_SECONDS=5
STARTUP_GRACE_SECONDS=12
MAX_FAILED_HEALTH_CHECKS=4
RESTART_INITIAL_DELAY_SECONDS=1
RESTART_MAX_DELAY_SECONDS=30
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"; cd "$ROOT"; mkdir -p logs runtime
LOG="$ROOT/logs/watchdog.log"; PY="$ROOT/.venv/bin/python"; child=""; stopping=false
log(){ printf '%s %s\n' "$(date -Is)" "$*" | tee -a "$LOG"; }
cleanup(){ stopping=true; [[ -n "$child" ]] && kill -TERM "$child" 2>/dev/null || true; [[ -n "$child" ]] && wait "$child" 2>/dev/null || true; exit 0; }
trap cleanup INT TERM
delay=$RESTART_INITIAL_DELAY_SECONDS
while ! $stopping; do
  log "Starting GPSPOOF"; "$PY" "$ROOT/gpspoof.py" >>"$LOG" 2>&1 & child=$!; sleep "$STARTUP_GRACE_SECONDS"; failures=0
  while kill -0 "$child" 2>/dev/null; do
    # WAITING_FOR_DEVICE is healthy; only the API's top-level healthy boolean controls restart.
    if "$PY" -c 'import json,sys,urllib.request
with urllib.request.urlopen(sys.argv[1], timeout=3) as response:
    payload = json.load(response)
raise SystemExit(0 if payload.get("healthy") else 1)' "$HEALTH_URL" >/dev/null 2>&1; then failures=0
    else failures=$((failures+1)); log "Health check failed ($failures/$MAX_FAILED_HEALTH_CHECKS)"; fi
    if ((failures>=MAX_FAILED_HEALTH_CHECKS)); then log "Application genuinely wedged; terminating PID $child"; kill -TERM "$child" 2>/dev/null; sleep 3; kill -KILL "$child" 2>/dev/null || true; break; fi
    sleep "$CHECK_INTERVAL_SECONDS"
  done
  wait "$child" 2>/dev/null; status=$?; child=""; $stopping && break
  log "GPSPOOF exited status=$status; restart in ${delay}s"; sleep "$delay"; delay=$((delay*2)); ((delay>RESTART_MAX_DELAY_SECONDS)) && delay=$RESTART_MAX_DELAY_SECONDS
done
