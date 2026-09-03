# Kepler73 - sockets.py
# WebSocket live broadcast - pushes positions and alarms every second

import time
import traceback
import threading
from flask_socketio import SocketIO

from backend.config import TLE_UPDATE_INTERVAL

selected_satellite = {"name": ""}
alarm_state        = {"enabled_sats": None}  # None = all enabled

HEARTBEAT_TIMEOUT = 180  # seconds – mute alarms if no heartbeat for 3 minutes


def _auto_update_tle():
    """Refresh TLEs for the active module using the configured TLE source."""
    try:
        from datetime import datetime, timezone
        from api.routes import state
        from backend.celestrak import celestrak_up
        from backend import tle_sources
        from backend.config import TLE_STALE_SEC
        from sgp4.api import Satrec

        source = state["config"].get("tle_source", "celestrak")

        # One quick check: is Celestrak worth trying at all? If not (down or
        # rate-limiting us) we go straight to the cached SatNOGS catalog for
        # any stale satellite instead of burning ~20 s per fetch on timeouts.
        celestrak_ok = (source != "celestrak") or celestrak_up()

        mod  = state.get("active_module")
        sats = mod.get("satellites", []) if mod else []
        if not sats:
            return

        now = datetime.now(timezone.utc)
        updated = 0
        for sat in sats:
            norad_id = sat.get("norad_id")
            if not norad_id:
                continue

            # Only re-fetch element sets that are actually stale. Celestrak
            # publishes new elements ~every 2 h and asks clients not to poll
            # harder than the data changes; a day-old LEO TLE is fine.
            try:
                age = (now - tle_sources.tle_epoch_dt(sat.get("tle_line1", ""))).total_seconds()
                if 0 <= age < TLE_STALE_SEC:
                    continue
            except Exception:
                pass

            result = tle_sources.resolve_tle(norad_id, state["config"],
                                             allow_celestrak=celestrak_ok)
            if result:
                _, line1, line2 = result
                sat["tle_line1"] = line1
                sat["tle_line2"] = line2
                rec = Satrec.twoline2rv(line1, line2)
                state["sat_records"][norad_id] = rec
                updated += 1

        if updated:
            print(f"[Kepler73] Auto-updated TLEs: {updated}/{len(sats)}")

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