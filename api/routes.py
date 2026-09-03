# -*- coding: utf-8 -*-
"""
Kepler73 – REST API endpoints
"""

import os
import threading
import time as _time
from flask import (Blueprint, jsonify, request, render_template,
                   send_from_directory, make_response)
from sgp4.api import Satrec

import backend.modules as mod_mgr
import backend.celestrak as celestrak
import backend.tle_sources as tle_sources
import backend.satcat as satcat
from backend.sgp4_engine import (propagate, forward_track, find_next_pass,
                                  get_pass_track, eci_to_azel, jd_to_dt,
                                  now_jd, compass, footprint_km,
                                  radial_velocity_ms)
from backend.config import (APP_NAME, APP_VERSION, TILE_DIR,
                             PASS_MIN_EL, TRACK_AHEAD_MIN)

bp = Blueprint("main", __name__)

# ── App state (shared with sockets.py) ───────────────────────
state = {
    "active_module": None,   # dict
    "sat_records":   {},     # norad_id → Satrec
    "config":        mod_mgr.load_config(),
    "online":        False,
}

# Short-lived cache of /api/passes/<id> results (see get_next_pass).
_pass_cache: dict = {}
_PASS_TTL = 120   # seconds


def _reload_records():
    state["sat_records"].clear()
    _pass_cache.clear()          # element sets changed – drop cached pass predictions
    mod = state["active_module"]
    if not mod:
        return
    for sat in mod.get("satellites", []):
        try:
            rec = Satrec.twoline2rv(sat["tle_line1"], sat["tle_line2"])
            state["sat_records"][sat["norad_id"]] = rec
        except Exception:
            pass


def _restore_active_module():
    modules = mod_mgr.list_modules()
    active  = state["config"].get("active_module")
    if active and active in modules:
        state["active_module"] = mod_mgr.load_module(active)
    elif modules:
        state["active_module"] = mod_mgr.load_module(modules[0])
    else:
        m = mod_mgr.create_module("My first module")
        state["active_module"] = m
    _reload_records()
    # Network status in the background
    threading.Thread(target=_check_network, daemon=True).start()


def _check_network():
    state["online"] = celestrak.check_online()


_restore_active_module()


# ── Main HTML ────────────────────────────────────────────────

@bp.route("/")
def index():
    # index.html has no ?v= cache-buster of its own, so make sure the browser
    # always revalidates it – otherwise a stale cached page keeps loading old
    # JS/CSS and half-applied UI changes after an update.
    resp = make_response(render_template("index.html"))
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


# ── App info ─────────────────────────────────────────────────

@bp.route("/api/info")
def api_info():
    return jsonify({
        "name":    APP_NAME,
        "version": APP_VERSION,
        "online":  state["online"],
        "config":  state["config"],
        "celestrak_blocked_until": celestrak.blocked_until(),   # unix ts, 0 if fine
    })


# ── Modules ──────────────────────────────────────────────────

@bp.route("/api/modules", methods=["GET"])
def get_modules():
    modules = mod_mgr.list_modules()
    active  = (state["active_module"] or {}).get("name")
    return jsonify({"modules": modules, "active": active})


@bp.route("/api/modules", methods=["POST"])
def create_module():
    name = (request.json or {}).get("name", "").strip()
    if not name:
        return jsonify({"error": "Name is required"}), 400
    if name in mod_mgr.list_modules():
        return jsonify({"error": f"'{name}' already exists"}), 409
    mod = mod_mgr.create_module(name)
    state["active_module"] = mod
    state["config"]["active_module"] = name
    mod_mgr.save_config(state["config"])
    _reload_records()
    return jsonify(mod), 201


@bp.route("/api/modules/<name>", methods=["GET"])
def get_module(name):
    try:
        return jsonify(mod_mgr.load_module(name))
    except FileNotFoundError:
        return jsonify({"error": "Not found"}), 404


@bp.route("/api/modules/<name>/activate", methods=["POST"])
def activate_module(name):
    try:
        state["active_module"] = mod_mgr.load_module(name)
        state["config"]["active_module"] = name
        mod_mgr.save_config(state["config"])
        _reload_records()
        return jsonify({"ok": True, "active": name})
    except FileNotFoundError:
        return jsonify({"error": "Not found"}), 404


@bp.route("/api/modules/<name>", methods=["DELETE"])
def delete_module(name):
    mod_mgr.delete_module(name)
    modules = mod_mgr.list_modules()
    if modules:
        state["active_module"] = mod_mgr.load_module(modules[0])
        state["config"]["active_module"] = modules[0]
    else:
        m = mod_mgr.create_module("Standard")
        state["active_module"] = m
        state["config"]["active_module"] = "Standard"
    mod_mgr.save_config(state["config"])
    _reload_records()
    return jsonify({"ok": True})


@bp.route("/api/modules/<name>/rename", methods=["POST"])
def rename_module(name):
    new_name = (request.json or {}).get("name", "").strip()
    if not new_name:
        return jsonify({"error": "New name is required"}), 400
    mod = mod_mgr.rename_module(name, new_name)
    state["active_module"] = mod
    state["config"]["active_module"] = new_name
    mod_mgr.save_config(state["config"])
    return jsonify(mod)


# ── Satellites ───────────────────────────────────────────────

@bp.route("/api/modules/<name>/satellites", methods=["POST"])
def add_satellite(name):
    sat = request.json
    if not sat or not sat.get("norad_id"):
        return jsonify({"error": "norad_id is required"}), 400
    try:
        mod = mod_mgr.load_module(name)
    except FileNotFoundError:
        return jsonify({"error": "Module not found"}), 404

    # No element set supplied (e.g. added from the SatNOGS lookup) – find one
    # now so the satellite can propagate. Try the (already cached) SatNOGS
    # catalog first, then the configured TLE source.
    if not sat.get("tle_line1") or not sat.get("tle_line2"):
        from backend.satnogs import catalog_tle
        hit = catalog_tle(sat["norad_id"])
        if hit:
            sat["tle_line1"], sat["tle_line2"] = hit
        else:
            resolved = tle_sources.resolve_tle(sat["norad_id"], state["config"])
            if not resolved:
                return jsonify({"error": "No orbital elements found for "
                                         f"NORAD {sat['norad_id']}"}), 404
            _, sat["tle_line1"], sat["tle_line2"] = resolved

    mod = mod_mgr.add_satellite(mod, sat)
    if state["active_module"] and state["active_module"]["name"] == name:
        state["active_module"] = mod
        _reload_records()
    return jsonify(mod), 201


@bp.route("/api/modules/<name>/satellites/<norad_id>", methods=["DELETE"])
def remove_satellite(name, norad_id):
    try:
        mod = mod_mgr.load_module(name)
    except FileNotFoundError:
        return jsonify({"error": "Module not found"}), 404
    mod = mod_mgr.remove_satellite(mod, norad_id)
    if state["active_module"] and state["active_module"]["name"] == name:
        state["active_module"] = mod
        _reload_records()
    return jsonify(mod)


@bp.route("/api/modules/<n>/satellites/<norad_id>/alarm", methods=["POST"])
def set_satellite_alarm(n, norad_id):
    """Persist alarm_enabled flag to module JSON."""
    try:
        mod = mod_mgr.load_module(n)
    except FileNotFoundError:
        return jsonify({"error": "Module not found"}), 404
    data = request.json or {}
    for sat in mod["satellites"]:
        if sat.get("norad_id") == norad_id:
            sat["alarm_enabled"] = bool(data.get("alarm_enabled", False))
            break
    mod_mgr.save_module(mod)
    if state["active_module"] and state["active_module"]["name"] == n:
        state["active_module"] = mod
    return jsonify({"ok": True})


# ── Satellite search (Celestrak or local file) ──────────────

@bp.route("/api/search")
def search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "Search term is required"}), 400

    cfg    = state["config"]
    source = cfg.get("tle_source", "celestrak")
    path   = cfg.get("tle_file", "")

    if source == "file" and not path:
        return jsonify({"error": "TLE source is 'Local file' but no file is "
                                 "configured (Settings > TLE file / folder)"}), 400

    if source in ("file", "file_then_celestrak") and path:
        try:
            results = tle_sources.search_file(path, q)
        except (FileNotFoundError, ValueError) as e:
            if source == "file":
                return jsonify({"error": str(e)}), 400
            results = []
        if results or source == "file":
            return jsonify({"results": results})

    try:
        results = celestrak.search_tle(q)
    except ConnectionError as e:
        # A catalog-number search with no current elements is usually a
        # decayed object – check the SATCAT so we can still show a row.
        if q.isdigit():
            row = _satcat_row(q)
            if row:
                return jsonify({"results": [row]})
        return jsonify({"error": str(e)}), 503

    if not results and q.isdigit():
        row = _satcat_row(q)
        if row:
            results = [row]
    return jsonify({"results": results})


def _satcat_row(norad_id: str) -> dict | None:
    """Build a non-addable search row from the SATCAT for a catalog number
    that has no current orbital elements (decayed, or unknown)."""
    rec = celestrak.lookup_satcat(norad_id)
    if not rec:
        return None
    decay = rec.get("DECAY_DATE")
    return {
        "name":      rec.get("OBJECT_NAME") or f"NORAD {norad_id}",
        "norad_id":  str(rec.get("NORAD_CAT_ID") or norad_id),
        "tle_line1": "", "tle_line2": "", "tle_epoch": "",
        "unavailable": True,
        "reason":    f"re-entered {decay}" if decay else "no current elements",
    }


@bp.route("/api/satcat/<norad_id>")
def api_satcat(norad_id):
    """Decay status for a satellite (SATCAT-backed, cached). Used by the
    sidebar to grey out decayed satellites.

    Answers from cache immediately; a stale/missing entry is refreshed in a
    background thread so the sidebar never blocks on a SATCAT round-trip
    (which is a 10 s timeout whenever Celestrak is unreachable)."""
    if request.args.get("refresh", "0") == "1":
        return jsonify(satcat.get_status(norad_id, force_refresh=True))

    cached = satcat.cached_status(norad_id)
    if cached is not None:
        return jsonify(cached)

    satcat.refresh_async(norad_id)
    return jsonify({"norad_id": str(norad_id), "name": "",
                    "decayed": None, "decay_date": None})


@bp.route("/api/tle/import", methods=["POST"])
def import_tle():
    """Parse pasted text or an uploaded file (TLE/3LE, GP JSON or GP CSV) and
    return the satellites in the same shape as /api/search, so the search
    dialog can list them for the user to add."""
    text = ""
    if request.files.get("file"):
        text = request.files["file"].read().decode("utf-8", "replace")
    else:
        data = request.get_json(silent=True) or {}
        if data.get("text"):
            text = data["text"]
        elif data.get("path"):
            try:
                return jsonify({"results": tle_sources.load_file_catalog(data["path"])})
            except (FileNotFoundError, ValueError) as e:
                return jsonify({"error": str(e)}), 400

    if not text.strip():
        return jsonify({"error": "No TLE data provided"}), 400
    try:
        results = tle_sources.parse_any(text)
    except Exception as e:
        return jsonify({"error": f"Could not parse TLE data: {e}"}), 400
    if not results:
        return jsonify({"error": "No satellites found in the supplied data"}), 400
    return jsonify({"results": results})


# ── Live positions ────────────────────────────────────────────

@bp.route("/api/positions")
def get_positions():
    """Returns current positions for all satellites in active module.
    Optional query param: selected=<name> to get 3-orbit track for that satellite."""
    selected = request.args.get("selected", "")
    positions = _compute_positions(selected)
    return jsonify(positions)


def _compute_positions(selected: str = "") -> dict:
    mod = state["active_module"]
    if not mod:
        return {}
    jd      = now_jd()
    cfg     = state["config"]
    obs_lat = cfg.get("lat", 59.33)
    obs_lon = cfg.get("lon", 18.07)
    obs_alt = cfg.get("alt", 20.0)

    result = {}
    for sat in mod.get("satellites", []):
        norad = sat["norad_id"]
        st = satcat.cached_status(norad)
        if st and st.get("decayed"):
            continue          # decayed object – no meaningful position
        rec   = state["sat_records"].get(norad)
        if not rec:
            continue
        res = propagate(rec, jd)
        if not res:
            continue
        lat, lon, alt, x, y, z, vx, vy, vz = res
        az, el, rng = eci_to_azel(obs_lat, obs_lon, obs_alt/1000,
                                   x, y, z, jd)
        v_radial = radial_velocity_ms(obs_lat, obs_lon, obs_alt,
                                      x, y, z, vx, vy, vz, jd)

        # Show 3 full orbits (~270 min) for selected satellite, 90 min for others
        is_selected  = (sat["name"] == selected)
        track_minutes = 270 if is_selected else TRACK_AHEAD_MIN
        track = forward_track(rec, jd, track_minutes)
        fp    = footprint_km(alt)

        result[sat["name"]] = {
            "lat":          round(lat, 4),
            "lon":          round(lon, 4),
            "alt":          round(alt, 1),
            "az":           round(az, 1),
            "el":           round(el, 1),
            "range_km":     round(rng),
            "v_radial":     round(v_radial, 1),
            "color":        sat.get("color", "#00e676"),
            "norad_id":     norad,
            "track":        track,
            "footprint_km": round(fp),
            "selected":     is_selected,
        }
    return result


# ── Next pass ─────────────────────────────────────────────────

# The sidebar asks for the next pass of every satellite on each list reload
# (module switch, add, delete) and again every 5 min. The answer barely moves,
# so `_pass_cache` (defined near `state`) memoises it briefly per
# (satellite, observer) to keep those bursts cheap.

@bp.route("/api/passes/<norad_id>")
def get_next_pass(norad_id):
    rec = state["sat_records"].get(norad_id)
    if not rec:
        return jsonify({"error": "Satellite not loaded"}), 404
    cfg = state["config"]
    lat = cfg.get("lat", 59.33)
    lon = cfg.get("lon", 18.07)
    alt = cfg.get("alt", 20.0)
    min_el = cfg.get("min_el", PASS_MIN_EL)

    key = (norad_id, round(lat, 3), round(lon, 3), round(alt), round(min_el, 1))
    hit = _pass_cache.get(key)
    if hit and _time.time() - hit[0] < _PASS_TTL:
        status, payload = hit[1]
        return jsonify(payload), status

    result = find_next_pass(rec, lat, lon, alt, min_el)
    if not result:
        resp = (404, {"error": "No pass within 7 days"})
    else:
        result["aos_compass"] = compass(result["aos_az"])
        result["los_compass"] = compass(result["los_az"])
        result["track"] = get_pass_track(rec, lat, lon, alt, result)
        resp = (200, result)

    _pass_cache[key] = (_time.time(), resp)
    if len(_pass_cache) > 400:
        _pass_cache.clear()
    return jsonify(resp[1]), resp[0]


# ── Pass from specific date (simulator) ──────────────────────

def _sim_satrec(norad_id, when_dt):
    """Satrec to use when simulating a pass at ``when_dt``: the nearest-epoch
    element set from the configured local TLE folder if one is set, otherwise
    the currently-loaded element set. Propagating today's TLE months into the
    past is meaningless, so a folder of dated TLE files makes historical
    simulation accurate."""
    cfg  = state["config"]
    src  = cfg.get("tle_source", "celestrak")
    path = cfg.get("tle_file", "")
    if src in ("file", "file_then_celestrak") and path:
        try:
            rec = tle_sources.find_in_file_near(path, norad_id, when_dt)
        except (FileNotFoundError, ValueError):
            rec = None
        if rec:
            try:
                return Satrec.twoline2rv(rec["tle_line1"], rec["tle_line2"])
            except Exception:
                pass
    return state["sat_records"].get(norad_id)


@bp.route("/api/passes/<norad_id>/from")
def get_pass_from(norad_id):
    """Find next pass starting from a given UTC datetime."""
    from sgp4.api import jday as sgp4_jday

    # Parse start datetime from query param: ?dt=2026-04-05T12:00:00
    dt_str = request.args.get("dt", "")
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        jd_day, jd_frac = sgp4_jday(dt.year, dt.month, dt.day,
                                     dt.hour, dt.minute, dt.second)
        start_jd = jd_day + jd_frac
    except Exception:
        return jsonify({"error": "Invalid datetime format"}), 400

    rec = _sim_satrec(norad_id, dt)
    if not rec:
        return jsonify({"error": "Satellite not loaded"}), 404

    cfg    = state["config"]
    result = find_next_pass(
        rec,
        cfg.get("lat", 59.33),
        cfg.get("lon", 18.07),
        cfg.get("alt", 20.0),
        cfg.get("min_el", PASS_MIN_EL),
        start_jd=start_jd,
    )
    if not result:
        return jsonify({"error": "No pass found from this time"}), 404

    result["aos_compass"] = compass(result["aos_az"])
    result["los_compass"] = compass(result["los_az"])
    result["track"] = get_pass_track(
        rec,
        cfg.get("lat", 59.33),
        cfg.get("lon", 18.07),
        cfg.get("alt", 20.0),
        result
    )
    try:
        result["tle_epoch_used"] = jd_to_dt(
            rec.jdsatepoch + rec.jdsatepochF).strftime("%Y-%m-%d")
    except Exception:
        pass
    return jsonify(result)


@bp.route("/api/passes/<norad_id>/position")
def get_position_at(norad_id):
    """Get satellite az/el/lat/lon at a specific JD."""
    try:
        jd = float(request.args.get("jd", 0))
    except Exception:
        return jsonify({"error": "Invalid jd"}), 400

    rec = _sim_satrec(norad_id, jd_to_dt(jd))
    if not rec:
        return jsonify({"error": "Satellite not loaded"}), 404

    cfg = state["config"]
    res = propagate(rec, jd)
    if not res:
        return jsonify({"error": "Propagation failed"}), 500

    az, el, rng = eci_to_azel(
        cfg.get("lat", 59.33),
        cfg.get("lon", 18.07),
        cfg.get("alt", 20.0) / 1000.0,
        res[3], res[4], res[5], jd
    )
    return jsonify({
        "lat": round(res[0], 4),
        "lon": round(res[1], 4),
        "alt": round(res[2], 1),
        "az":  round(az, 1),
        "el":  round(el, 1),
    })


@bp.route("/api/tle/refresh", methods=["POST"])
def refresh_tle():
    """Re-fetch TLEs for every satellite in the active module, using the
    configured TLE source (Celestrak and/or a local file).

    When the source can hit Celestrak, a manual refresh is gated to once per
    TLE_MANUAL_COOLDOWN_SEC – Celestrak temporarily blocks IPs that request
    more often than the data changes (~2 h). Pass {"force": true} to override
    (the UI asks the user to confirm)."""
    from backend.config import TLE_MANUAL_COOLDOWN_SEC
    mod = state["active_module"]
    if not mod:
        return jsonify({"error": "No active module"}), 400

    force  = bool((request.json or {}).get("force"))
    src    = state["config"].get("tle_source", "celestrak")
    last   = float(state["config"].get("tle_last_refresh", 0) or 0)
    since  = _time.time() - last
    if (not force and src in ("celestrak", "file_then_celestrak")
            and since < TLE_MANUAL_COOLDOWN_SEC):
        wait_min = int((TLE_MANUAL_COOLDOWN_SEC - since) / 60) + 1
        return jsonify({
            "cooldown": True,
            "wait_minutes": wait_min,
            "last_refresh_min_ago": int(since / 60),
            "error": (f"Last TLE update was {int(since/60)} min ago. Celestrak "
                      f"rate-limits frequent requests – wait ~{wait_min} min "
                      f"more, or force it anyway."),
        }), 429

    sats    = mod.get("satellites", [])
    updated = 0
    failed  = []

    # One up-front check: if Celestrak can't be reached, skip it (going straight
    # to the cached SatNOGS catalog) instead of eating a ~20 s timeout per sat.
    allow_ct = (src == "file") or celestrak.celestrak_up()

    for sat in sats:
        norad = sat["norad_id"]
        result = tle_sources.resolve_tle(norad, state["config"], allow_celestrak=allow_ct)
        if not result:
            failed.append(sat["name"])
            continue
        # Only count it as an update if the element set actually got newer.
        try:
            old_ep = tle_sources.tle_epoch_dt(sat.get("tle_line1", ""))
            new_ep = tle_sources.tle_epoch_dt(result[1])
            newer = new_ep > old_ep
        except Exception:
            newer = True
        sat["tle_line1"], sat["tle_line2"] = result[1], result[2]
        if newer:
            updated += 1

    if updated:
        mod_mgr.save_module(mod)
        _reload_records()

    state["config"]["tle_last_refresh"] = _time.time()
    mod_mgr.save_config(state["config"])

    resp = {"updated": updated, "failed": failed, "total": len(sats)}
    if celestrak.is_blocked():
        resp["note"] = ("Celestrak is rate-limiting this IP – used cached / "
                        "SatNOGS elements where possible.")
    return jsonify(resp)

@bp.route("/api/tle/export")
def export_tle():
    """Download the loaded element sets as a 3-line TLE file (name + 2 lines
    per satellite). `?all=1` exports every module, otherwise the active one.
    The file is a plain `.txt` – that is the extension Gpredict's "Update TLE
    from local files" scans a folder for (it ignores `.tle` in some builds)."""
    from flask import Response
    all_mods = request.args.get("all") == "1"

    seen = {}
    if all_mods:
        for name in mod_mgr.list_modules():
            try:
                m = mod_mgr.load_module(name)
            except FileNotFoundError:
                continue
            for s in m.get("satellites", []):
                seen[s["norad_id"]] = s
        fname = "kepler73_all.txt"
    else:
        mod = state["active_module"]
        for s in (mod.get("satellites", []) if mod else []):
            seen[s["norad_id"]] = s
        base = (mod or {}).get("name", "module").replace(" ", "_")
        fname = f"kepler73_{base}.txt"

    lines = []
    for s in seen.values():
        l1, l2 = s.get("tle_line1"), s.get("tle_line2")
        if l1 and l2:
            lines += [s.get("name") or f"NORAD {s['norad_id']}", l1, l2]

    body = "\n".join(lines) + ("\n" if lines else "")
    return Response(body, mimetype="text/plain",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@bp.route("/api/transmitters/<norad_id>")
def get_transmitters(norad_id):
    """Returns cached transponder list from SatNOGS for a satellite."""
    from backend.satnogs import get_transponders
    force = request.args.get("refresh", "0") == "1"
    try:
        data = get_transponders(norad_id, force_refresh=force)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 503


@bp.route("/api/satnogs/catalog")
def satnogs_catalog():
    """Full SatNOGS catalog (satellites x transmitters) for the lookup dialog.
    ?refresh=1 re-downloads the three source lists."""
    from backend.satnogs import get_catalog
    try:
        return jsonify(get_catalog(force_refresh=request.args.get("refresh") == "1"))
    except Exception as e:
        return jsonify({"error": f"SatNOGS unavailable: {e}"}), 503


# ── Settings ──────────────────────────────────────────────────

@bp.route("/api/config", methods=["GET"])
def get_config():
    return jsonify(state["config"])


@bp.route("/api/config", methods=["POST"])
def save_config():
    data = request.json or {}
    for key in ["lat", "lon", "alt", "min_el"]:
        if key in data:
            try:
                state["config"][key] = float(data[key])
            except (ValueError, TypeError):
                pass
    if "alarm_min" in data:
        try:
            state["config"]["alarm_min"] = int(data["alarm_min"])
        except (ValueError, TypeError):
            pass
    if "alarm_sound" in data:
        state["config"]["alarm_sound"] = str(data["alarm_sound"])
    if "tle_source" in data:
        src = str(data["tle_source"])
        if src in ("celestrak", "file", "file_then_celestrak"):
            state["config"]["tle_source"] = src
    if "tle_file" in data:
        state["config"]["tle_file"] = str(data["tle_file"]).strip()
    mod_mgr.save_config(state["config"])
    return jsonify({"ok": True, "config": state["config"]})


# ── Location picker (geocoding + elevation) ──────────────────

@bp.route("/api/geocode")
def api_geocode():
    """Place-name search for the observer-location map picker."""
    from backend import geocode as geo
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"results": []})
    try:
        return jsonify({"results": geo.geocode(q)})
    except ConnectionError as e:
        return jsonify({"error": str(e)}), 503


@bp.route("/api/elevation")
def api_elevation():
    """Ground elevation (m above sea level) for a lat/lon."""
    from backend import geocode as geo
    try:
        lat = float(request.args.get("lat"))
        lon = float(request.args.get("lon"))
    except (TypeError, ValueError):
        return jsonify({"error": "lat and lon are required"}), 400
    elev = geo.elevation(lat, lon)
    if elev is None:
        return jsonify({"error": "Elevation unavailable"}), 503
    return jsonify({"elevation": round(elev, 1)})


# ── Heartbeat ────────────────────────────────────────────────

_last_heartbeat = _time.time()

@bp.route("/api/heartbeat", methods=["POST"])
def heartbeat():
    global _last_heartbeat
    _last_heartbeat = _time.time()
    return jsonify({"ok": True})


# ── Quit ─────────────────────────────────────────────────────

@bp.route("/api/quit", methods=["POST"])
def quit_app():
    """Gracefully shut down the Flask server."""
    import threading, os, signal
    def _shutdown():
        import time
        time.sleep(0.5)
        os.kill(os.getpid(), signal.SIGTERM)
    threading.Thread(target=_shutdown, daemon=True).start()
    return jsonify({"ok": True})


# ── Tile-proxy (OSM + Esri cache) ────────────────────────────

EMPTY_PNG = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01'
    b'\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89'
    b'\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01'
    b'\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
)


@bp.route("/tiles/<layer>/<int:z>/<int:x>/<int:y>.png")
def tile(layer, z, x, y):
    from flask import Response
    cache = os.path.join(TILE_DIR, layer, str(z), str(x), f"{y}.png")

    if os.path.exists(cache):
        with open(cache, "rb") as f:
            return Response(f.read(), mimetype="image/png")

    # Fetch from upstream
    data = _fetch_tile(layer, z, x, y)
    if data:
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        with open(cache, "wb") as f:
            f.write(data)
        return Response(data, mimetype="image/png")

    return Response(EMPTY_PNG, mimetype="image/png")


def _fetch_tile(layer, z, x, y):
    import urllib.request
    try:
        if layer == "satellite":
            url = (f"https://server.arcgisonline.com/ArcGIS/rest/services/"
                   f"World_Imagery/MapServer/tile/{z}/{y}/{x}")
        else:
            url = f"https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        req = urllib.request.Request(
            url, headers={"User-Agent": "Kepler73/0.3 (amateur radio)"})
        with urllib.request.urlopen(req, timeout=6) as r:
            return r.read()
    except Exception:
        return None