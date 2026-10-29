# Kepler73 - sockets.py
# WebSocket live broadcast - pushes positions and alarms every second

import time
import traceback
import threading
from flask_socketio import SocketIO

from backend.config import TLE_UPDATE_INTERVAL
from backend import doppler, rotor
from backend.hamlib import RigctldClient
from backend.rotctld import RotctldClient

selected_satellite = {"name": ""}
alarm_state        = {"enabled_sats": None}  # None = all enabled

HEARTBEAT_TIMEOUT = 180  # seconds – mute alarms if no heartbeat for 3 minutes


# ── Radio (CAT) Doppler control ─────────────────────────────────
# rig_cfg is the live working copy; a mirror is persisted in app_config.json
# under the "rig_*" keys (see api.routes.set_rig_config / modules.load_config).
rig_cfg = {
    "enabled":     False,
    "host":        "127.0.0.1",
    "port":        4532,
    "down_hz":     0.0,     # nominal downlink of the selected satellite
    "up_hz":       0.0,     # nominal uplink (only used when tx_enabled)
    "tx_enabled":  False,
    "deadband_hz": 10.0,    # don't retune for a change smaller than this
    "bands":       "",      # MHz ranges the radio covers, e.g. "144-146, 430-440"
                            # (display-only: greys out transponders it can't reach)
}
rig_status = {
    "enabled":   False,
    "connected": False,
    "error":     "",
    "sat":       "",
    "v_radial":  0.0,
    "rx_cmd_hz": 0.0,       # last frequency sent to the rig for RX
    "tx_cmd_hz": 0.0,
    "rig_hz":    0.0,       # frequency read back from the rig
    "mode":      "",        # rig mode read back (USB / LSB / CW / FM / WFM ...)
    "passband_hz": 0,       # rig filter passband, Hz
}

_rig_target = {"rx": 0.0, "tx": 0.0}
_rig_lock   = threading.Lock()
_rig_wake   = threading.Event()


def apply_rig_cfg(new: dict):
    """Merge a partial config dict (keys as in rig_cfg) into the live config."""
    if new.get("host"):
        rig_cfg["host"] = str(new["host"]).strip()
    if "port" in new:
        try:
            rig_cfg["port"] = int(new["port"])
        except (TypeError, ValueError):
            pass
    for k in ("down_hz", "up_hz", "deadband_hz"):
        if k in new:
            try:
                rig_cfg[k] = max(0.0, float(new[k]))
            except (TypeError, ValueError):
                pass
    for k in ("enabled", "tx_enabled"):
        if k in new:
            rig_cfg[k] = bool(new[k])
    if "bands" in new:
        rig_cfg["bands"] = str(new["bands"]).strip()
    _rig_wake.set()


def _update_rig(positions: dict):
    """Called from the 1 Hz loop: hand the selected satellite's range-rate to
    the rig worker as a target frequency. No socket I/O happens here."""
    if not rig_cfg["enabled"]:
        rig_status["enabled"] = False
        return
    rig_status["enabled"] = True
    sel = selected_satellite["name"]
    p   = positions.get(sel) if positions else None
    if not p or rig_cfg["down_hz"] <= 0:
        with _rig_lock:
            _rig_target["rx"] = _rig_target["tx"] = 0.0
        rig_status.update(sat=sel or "", v_radial=0.0)
        return
    v  = float(p.get("v_radial", 0.0))
    rx = doppler.rx_freq(rig_cfg["down_hz"], v)
    tx = doppler.tx_freq(rig_cfg["up_hz"], v) if rig_cfg["up_hz"] > 0 else 0.0
    with _rig_lock:
        _rig_target["rx"] = rx
        _rig_target["tx"] = tx
    rig_status.update(sat=sel, v_radial=round(v, 1))
    _rig_wake.set()


def _rig_worker():
    """Owns the blocking socket I/O to rigctld so the 1 Hz loop never stalls."""
    client  = None
    last_rx = 0.0
    last_tx = 0.0
    tick    = 0
    while True:
        _rig_wake.wait(timeout=1.0)
        _rig_wake.clear()
        tick += 1

        if not rig_cfg["enabled"]:
            if client:
                client.close()
                client = None
            rig_status.update(enabled=False, connected=False, mode="", passband_hz=0)
            continue
        rig_status["enabled"] = True

        if (client is None or client.host != rig_cfg["host"]
                or client.port != int(rig_cfg["port"])):
            if client:
                client.close()
            client  = RigctldClient(rig_cfg["host"], rig_cfg["port"])
            last_rx = last_tx = 0.0

        with _rig_lock:
            rx = _rig_target["rx"]
            tx = _rig_target["tx"]
        if rx <= 0:
            continue

        db = float(rig_cfg["deadband_hz"])
        if abs(rx - last_rx) >= db:
            if client.set_freq(rx):
                last_rx = rx
                rig_status.update(connected=True, error="", rx_cmd_hz=rx)
            else:
                rig_status.update(connected=False, error=client.last_error)
                continue

        if rig_cfg["tx_enabled"] and tx > 0 and abs(tx - last_tx) >= db:
            if client.set_split_freq(tx):
                last_tx = tx
                rig_status["tx_cmd_hz"] = tx

        if tick % 5 == 0:            # light read-back, ~every 5 s
            fb = client.get_freq()
            if fb:
                rig_status["rig_hz"] = fb
            md = client.get_mode()
            if md:
                rig_status["mode"] = md[0]
                rig_status["passband_hz"] = md[1] or 0


# ── Rotator (Az/El) tracking ───────────────────────────────────
# Same shape as the rig block above: rot_cfg is the live working copy, mirrored
# to app_config.json under "rot_*" keys.
rot_cfg = {
    "enabled":     False,
    "host":        "127.0.0.1",
    "port":        4533,
    "min_el":      3.0,     # track only while the satellite is above this
    "park_az":     0.0,
    "park_el":     0.0,
    "az_deadband": 2.0,     # don't re-command for a smaller move
    "el_deadband": 2.0,
}
rot_status = {
    "enabled":   False,
    "connected": False,
    "error":     "",
    "sat":       "",
    "mode":      "idle",    # idle | tracking | parked
    "cmd_az":    0.0,
    "cmd_el":    0.0,
    "rot_az":    0.0,       # read back from the rotator
    "rot_el":    0.0,
}
_rot_target = {"pos": None}   # (az, el) the rotor should point at, or None
_rot_lock   = threading.Lock()
_rot_wake   = threading.Event()


def apply_rot_cfg(new: dict):
    if new.get("host"):
        rot_cfg["host"] = str(new["host"]).strip()
    if "port" in new:
        try:
            rot_cfg["port"] = int(new["port"])
        except (TypeError, ValueError):
            pass
    for k in ("min_el", "park_az", "park_el", "az_deadband", "el_deadband"):
        if k in new:
            try:
                rot_cfg[k] = float(new[k])
            except (TypeError, ValueError):
                pass
    if "enabled" in new:
        rot_cfg["enabled"] = bool(new["enabled"])
    _rot_wake.set()


def _update_rotor(positions):
    """1 Hz loop hook: hand the selected satellite's az/el to the rotor worker
    as a desired pointing. No socket I/O here."""
    if not rot_cfg["enabled"]:
        rot_status.update(enabled=False, mode="idle")
        return
    rot_status["enabled"] = True
    sel = selected_satellite["name"]
    p   = positions.get(sel) if positions else None
    if not p:
        with _rot_lock:
            _rot_target["pos"] = None
        rot_status.update(sat=sel or "", mode="idle")
        return
    az, el, mode = rotor.desired_pointing(float(p.get("az", 0.0)),
                                          float(p.get("el", 0.0)), rot_cfg)
    with _rot_lock:
        _rot_target["pos"] = (az, el)
    rot_status.update(sat=sel, mode=mode)
    _rot_wake.set()


def _rotor_worker():
    """Owns the blocking socket I/O to rotctld so the 1 Hz loop never stalls."""
    client   = None
    last_cmd = None
    tick     = 0
    while True:
        _rot_wake.wait(timeout=1.0)
        _rot_wake.clear()
        tick += 1

        if not rot_cfg["enabled"]:
            if client:
                client.close()
                client = None
            last_cmd = None
            rot_status.update(enabled=False, connected=False, mode="idle")
            continue
        rot_status["enabled"] = True

        if (client is None or client.host != rot_cfg["host"]
                or client.port != int(rot_cfg["port"])):
            if client:
                client.close()
            client   = RotctldClient(rot_cfg["host"], rot_cfg["port"])
            last_cmd = None

        with _rot_lock:
            tgt = _rot_target["pos"]
        if tgt is None:
            continue

        if rotor.beyond_deadband(tgt, last_cmd,
                                 float(rot_cfg["az_deadband"]),
                                 float(rot_cfg["el_deadband"])):
            if client.set_pos(tgt[0], tgt[1]):
                last_cmd = tgt
                rot_status.update(connected=True, error="",
                                  cmd_az=round(tgt[0], 1), cmd_el=round(tgt[1], 1))
            else:
                rot_status.update(connected=False, error=client.last_error)
                continue

        if tick % 3 == 0:            # rotors move slowly – read back ~every 3 s
            pos = client.get_pos()
            if pos:
                rot_status.update(rot_az=round(pos[0], 1), rot_el=round(pos[1], 1))


def _auto_update_tle():
    """Background TLE refresh for ALL modules using the configured TLE source.

    With Celestrak this is at most one bulk catalog download per BULK_REFRESH_SEC
    (the first sweep after start-up fetches it if there is none), after which
    every module is updated from that local copy – the number of Celestrak
    requests no longer depends on how many satellites you track."""
    try:
        from api.routes import state, update_all_module_tles
        from backend.celestrak import celestrak_up
        from backend import tle_sources

        source = state["config"].get("tle_source", "celestrak")

        # One quick check: is Celestrak worth trying at all? If not (down or
        # rate-limiting us) we use the local catalogs only, instead of burning
        # long timeouts on every request.
        celestrak_ok = (source == "file") or celestrak_up()

        if source in ("file", "file_then_celestrak"):
            # the user's own sources (files are read live; URLs are re-downloaded)
            tle_sources.refresh_url_sources(state["config"].get("tle_file", ""))

        if source in ("celestrak", "file_then_celestrak") and celestrak_ok:
            tle_sources.refresh_bulk()      # no-op while the cached copy is fresh

        res = update_all_module_tles(allow_celestrak=celestrak_ok, only_stale=True)
        if res["updated"]:
            print(f"[Kepler73] Auto-updated TLEs: {res['updated']}/{res['total']} "
                  f"satellites across {res['modules']} modules")

    except Exception as e:
        print(f"[Kepler73] TLE auto-update error: {e}")


def start_position_broadcast(socketio: SocketIO):
    """Starts background thread that broadcasts positions and checks alarms."""

    def _loop():
        from backend.alarm import check_alarms
        from backend.sgp4_engine import find_next_pass, now_jd
        tick = 0
        last_tle_update = 0  # Force update on first tick

        while True:
            try:
                from api.routes import state, _compute_positions
                positions = _compute_positions(selected_satellite["name"])
                socketio.emit("satellite_positions", positions)

                # CAT Doppler: feed the rig worker, then push a status frame
                _update_rig(positions)
                socketio.emit("rig_status", {
                    **rig_status,
                    "down_hz":    rig_cfg["down_hz"],
                    "up_hz":      rig_cfg["up_hz"],
                    "tx_enabled": rig_cfg["tx_enabled"],
                    "bands":      rig_cfg["bands"],
                })

                # Az/El rotator: feed the rotor worker, then push its status
                _update_rotor(positions)
                socketio.emit("rotor_status", dict(rot_status))

                # Check network status every 60 seconds
                if tick % 60 == 0:
                    from backend.celestrak import check_online
                    from api.routes import state as _state
                    _state["online"] = check_online()

                # Auto-update TLEs on startup and every hour
                now = time.time()
                if now - last_tle_update >= TLE_UPDATE_INTERVAL:
                    threading.Thread(target=_auto_update_tle, daemon=True).start()
                    last_tle_update = now

                # Check alarms every 5 seconds
                if tick % 5 == 0:
                    # Suppress alarms if browser has been closed (no heartbeat)
                    from api.routes import _last_heartbeat
                    browser_active = (time.time() - _last_heartbeat) < HEARTBEAT_TIMEOUT

                    mod  = state.get("active_module")
                    sats = mod.get("satellites", []) if mod else []

                    enabled = alarm_state["enabled_sats"]
                    if enabled is not None:
                        sats = [s for s in sats if s["norad_id"] in enabled]

                    if sats and browser_active:
                        events = check_alarms(
                            state["sat_records"],
                            sats,
                            state["config"],
                            now_jd,
                            find_next_pass
                        )
                        for ev in events:
                            socketio.emit("pass_alarm", ev)

            except Exception:
                traceback.print_exc()

            tick += 1
            time.sleep(1)

    t = threading.Thread(target=_loop, daemon=True)
    t.start()

    # Restore persisted CAT settings, then start the rig worker.
    try:
        from api.routes import state as _st
        _c = _st["config"]
        apply_rig_cfg({
            "host":        _c.get("rig_host", rig_cfg["host"]),
            "port":        _c.get("rig_port", rig_cfg["port"]),
            "down_hz":     _c.get("rig_down_hz", 0.0),
            "up_hz":       _c.get("rig_up_hz", 0.0),
            "tx_enabled":  _c.get("rig_tx_doppler", False),
            "deadband_hz": _c.get("rig_deadband_hz", rig_cfg["deadband_hz"]),
            "bands":       _c.get("rig_bands", ""),
            "enabled":     _c.get("rig_enabled", False),
        })
    except Exception:
        traceback.print_exc()
    threading.Thread(target=_rig_worker, daemon=True).start()

    # Restore persisted rotator settings, then start the rotor worker.
    try:
        from api.routes import state as _st_r
        _cr = _st_r["config"]
        apply_rot_cfg({
            "host":        _cr.get("rot_host", rot_cfg["host"]),
            "port":        _cr.get("rot_port", rot_cfg["port"]),
            "min_el":      _cr.get("rot_min_el", rot_cfg["min_el"]),
            "park_az":     _cr.get("rot_park_az", 0.0),
            "park_el":     _cr.get("rot_park_el", 0.0),
            "az_deadband": _cr.get("rot_az_deadband", rot_cfg["az_deadband"]),
            "el_deadband": _cr.get("rot_el_deadband", rot_cfg["el_deadband"]),
            "enabled":     _cr.get("rot_enabled", False),
        })
    except Exception:
        traceback.print_exc()
    threading.Thread(target=_rotor_worker, daemon=True).start()

    @socketio.on("set_selected")
    def on_set_selected(data):
        selected_satellite["name"] = data.get("name", "")

    @socketio.on("set_muted")
    def on_set_muted(data):
        from backend.alarm import set_muted
        set_muted(bool(data.get("muted", False)))

    @socketio.on("set_alarm_minutes")
    def on_set_alarm_minutes(data):
        from backend.alarm import set_alarm_minutes
        set_alarm_minutes(int(data.get("minutes", 3)))

    @socketio.on("set_alarm_sound")
    def on_set_alarm_sound(data):
        from backend.alarm import set_sound
        set_sound(data.get("sound", "cw_aos"))

    @socketio.on("set_alarm_sats")
    def on_set_alarm_sats(data):
        from backend.alarm import reset_all_fired
        ids = data.get("norad_ids", None)
        alarm_state["enabled_sats"] = set(ids) if ids is not None else None
        reset_all_fired()