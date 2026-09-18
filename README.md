# GPSPOOF

GPSPOOF is a localhost web controller that keeps an explicitly selected simulated iPhone location alive over USB. It runs device discovery, an iOS 17+ Remote Service Discovery (RSD) tunnel, DVT, location simulation, reassertion, route playback, and recovery in **one asyncio process**. It does not need macOS, Xcode, a jailbreak, `tunneld`, or an open browser.

> Intended target: Debian/Ubuntu, Python 3.13, iPhone 14 / iOS 26.5. Physical verification on iOS 26.5 is still required because no phone is available to the automated test environment.

## Install and run

Enable **Developer Mode** on the phone, unlock it, connect by USB, accept **Trust This Computer**, then:

```bash
chmod +x setup.sh
./setup.sh
./run.sh
```

Open <http://127.0.0.1:5000>. `setup.sh` creates `.venv`, installs the pinned `pymobiledevice3 4.18.0`, checks usbmux safely, and runs tests. It never changes phone location. It prefers Python 3.13, uses `uv` to obtain it when `uv` is already installed, and otherwise uses a compatible system Python (3.11+). Edit the constants at the top of `setup.sh` if system packages must not be installed.

`run.sh` holds an advisory `flock`, runs `watchdog.sh`, and applies a process-lifetime `systemd-inhibit` sleep/idle inhibitor when available. It never changes permanent power settings. A genuinely suspended machine cannot maintain USB/RSD; some laptop firmware or desktop policies may override inhibition. Use `PREVENT_SLEEP=false ./run.sh` to skip it and `USE_WATCHDOG=false ./run.sh` to run directly.

Stop with Ctrl-C or SIGTERM. On a clean stop, GPSPOOF makes a bounded best-effort `clear()` call. In the interface, **Clear / Restore Real GPS** first atomically persists `active=false`, then clears the live channel; even a crash during clearing therefore cannot resurrect the old spoof.

## Architecture and persistence

Modern iOS developer services are not normal usbmux lockdown services. The pinned pymobiledevice3 API is used as nested async context managers:

```text
PreferredRsdTunnel(serial=UDID)
  └─ DvtProvider(rsd)
       └─ LocationSimulation(dvt)
```

On Linux `PreferredRsdTunnel` selects the supported userspace RSD route. The tunnel is process-local, so it is created and consumed in the web server's event loop. GPSPOOF neither launches nor depends on privileged `tunneld`; `ALLOW_TUNNELD_FALLBACK` and the legacy CLI backend are disabled and intentionally not selected silently.

The desired coordinate is independent of a connection and is atomically stored in `data/state.json`. A fresh session applies it immediately, performs the configured burst (0, 0.2, 0.6 and 1.5 seconds), and then sends serialized, timeout-bounded location calls at the effective interval. A browser can close without affecting this loop.

Any set timeout/transport exception poisons the **whole** session. GPSPOOF cancels its connection-scoped tasks, exits LocationSimulation, DVT, and RSD in reverse order, applies bounded reconnect backoff, creates three fresh objects, and restores the desired point. It never retries indefinitely on a broken DVT object. Unplugging enters `WAITING_FOR_DEVICE`; the remembered UDID prevents switching to another phone. Reconnect that same phone to restore the requested location.

Every five minutes by default, proactive recycling deliberately replaces the complete hierarchy without calling `clear()`. This helps with sessions that look alive while simulation has decayed. The internal watchdog independently notices a lack of meaningful progress while active and forces the same complete rebuild.

### Two different failures

* **Transport/session failure:** RSD, DVT, or the RPC really broke. GPSPOOF detects an exception/timeout/watchdog stall and rebuilds everything.
* **Silent GPS lapse:** host RPCs can still look healthy while the phone returns to real GPS. Host success cannot verify Core Location. Regular reassertion, proactive recycling, and the **Location bounced back** feedback are used; the UI continues to say confirmation is `UNKNOWN` until the user reports an observation.

Successful RPCs mean only `RPC_HEALTH=GOOD`, not “GPS spoof working.” If the phone never moves, use the explicit **Test location simulation** flow and choose `INJECTION_UNCONFIRMED`.

## Interface

Clicking or dragging the Leaflet/OpenStreetMap marker only selects a point. Press **Set Location** to activate it. The responsive dashboard includes connection/RSD/DVT state, desired point, last call, interval, session age/recycle countdown, live diagnostics/logs, stable/bounce reports, and downloadable sanitized JSON.

* **Joystick:** choose 1–100 m and use eight directions. Great-circle destination math accounts for longitude convergence. Arrow keys or WASD work outside form inputs.
* **Routes:** upload GPX, select speed/loop, then play, pause, resume, or stop. GPSPOOF parses and interpolates points internally and keeps the current desired route coordinate through a session rebuild. Progress is persisted for crash diagnosis/recovery.
* **Saved places:** name and store arbitrary points in atomic `data/locations.json`.
* **Capability test:** explicitly sets the selected point in a fresh session, retains it, and records one of stable, decay, unconfirmed injection, or transport failure. Nothing moves automatically during setup/startup unless `state.json` already records an active desired location.

## AUTO_TUNE

All important manual constants are together at the top of `gpspoof.py`. `AUTO_TUNE=True` stores a profile keyed by UDID, model, iOS/build, pymobiledevice3, and host platform. User bounce reports shorten the reassert interval (only through configured candidates), reduce recycle age, lower confidence, immediately reapply, and recycle. Stable reports raise confidence. Profiles and observations use atomic writes.

With `AUTO_TUNE=False`, profile settings are neither loaded as active settings nor written; exact manual reassert, RPC timeout, reconnect, burst and recycle constants are used. Automatic transport recovery and watchdogs remain enabled. The reliability score/confidence concerns host/session evidence plus human reports and never claims to measure actual Core Location.

`ENABLE_HEARTBEAT` is exposed, but 4.18.0's `HeartbeatService` selects a service based on provider type and owns its exchange loop rather than offering a passive DVT-channel ping. GPSPOOF conservatively relies on set RPCs, usbmux discovery, RSD lifetime, and its watchdog; it does not start a competing heartbeat on the active RSD provider. This avoids presenting heartbeat as GPS confirmation.

## Device preparation and real-device test

1. Run `.venv/bin/pymobiledevice3 usbmux list`. If absent, start `sudo systemctl enable --now usbmuxd`.
2. Unlock the phone and accept Trust. If needed run `.venv/bin/pymobiledevice3 lockdown pair`.
3. Enable Settings → Privacy & Security → Developer Mode. GPSPOOF never enables it because doing so can reboot the phone.
4. Ensure the Developer Disk Image/personalized DDI is available. Current pymobiledevice3 provides `.venv/bin/pymobiledevice3 mounter auto-mount` (network access may be needed).
5. Start GPSPOOF and inspect device/preflight, RSD, DVT, and LocationSimulation states.
6. Select a harmless test coordinate and explicitly press **Test location simulation**. Observe Maps on the phone and record the offered result.

The project follows the pinned release's current public imports. Upstream APIs can change; update the pin and inspect `remote/rsd_tunnel.py`, `remote/userspace_tunnel.py`, `services/dvt/instruments/location_simulation.py`, and `services/heartbeat.py` together before upgrading.

## Watchdog, state and logs

`watchdog.sh` launches one Python process, waits through startup, and polls `/api/health`. `WAITING_FOR_DEVICE` and idle `STOPPED` are valid healthy states, so an unplugged phone causes no restart. Four consecutive failed/unhealthy checks trigger TERM/KILL and bounded exponential restart delay. Process exit also triggers restart. Logs are `logs/watchdog.log`; app logs rotate in `logs/gpspoof.log`. The `run.sh` lock prevents duplicate watchdogs and servers.

An abnormal kill does not alter `state.json`; watchdog restart restores an active desired coordinate. Deliberate UI clearing persists inactive. `/api/diagnostics?download=true` contains status, versions, and non-secret configuration only—never pairing records, keys, or tokens; UDIDs are masked.

## Troubleshooting

| Symptom | Action |
|---|---|
| No device / usbmux unavailable | Check cable, `systemctl status usbmuxd`, then `pymobiledevice3 usbmux list`. |
| Trust/locked errors | Unlock, accept Trust, run `pymobiledevice3 lockdown pair`, and reconnect. |
| Developer Mode disabled | Enable it manually and complete the required reboot/confirmation. |
| Developer image unavailable | Run `pymobiledevice3 mounter auto-mount`; review network and pymobiledevice3 logs. |
| `Device is not connected` / RSD failure | Avoid stale external tunneld; stop it, reconnect USB, and press Reconnect. GPSPOOF creates fresh userspace RSD state. |
| DVT/channel failure | Check Developer Mode/DDI. GPSPOOF will discard and recreate the complete hierarchy. |
| `set()` succeeds but phone never moves | This is simulation unconfirmed, not transport success. Run the capability test and report **Phone did not move**. |
| Works then jumps back | Report **Location bounced back**; AUTO_TUNE becomes more aggressive and performs an immediate recycle/reapply. |
| Reconnect loop | Download diagnostics, inspect both logs, verify cable/DDI/unlock, and temporarily use fake mode to separate UI logic from the phone. |
| Watchdog restart loop | Run `USE_WATCHDOG=false ./run.sh`, inspect the foreground exception and `logs/watchdog.log`. |

## Development / fake mode

Set `FAKE_DEVICE_MODE=True` near the top of `gpspoof.py`, then run `./run.sh`. Fake mode opens isolated fake tunnel/DVT/location objects and supports UI, persistence, routes and recovery without an iPhone. Tests never need a phone:

```bash
.venv/bin/python -m pytest -q
python3 -m compileall -q .
bash -n setup.sh run.sh watchdog.sh
```

Leaflet tiles and assets load from public CDNs, so the map background needs Internet access; all GPS persistence remains server-side and local.
