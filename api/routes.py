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
import backend.datapack as datapack
import backend.updatecheck as updatecheck
from backend import netmode
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
netmode.set_offline(state["config"].get("offline_mode", False))

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


_tle_update_lock = threading.Lock()

# norad_id -> unix time of the last per-satellite Celestrak request for a
# satellite that is not in the bulk catalog. Persisted so a restart doesn't
# re-ask for every derelict at once; see update_all_module_tles().
_offcat_attempts: dict = {}
_offcat_loaded = False


def _offcat_file() -> str:
    from backend.config import TLE_DIR
    return os.path.join(TLE_DIR, "offcatalog_attempts.json")


def _offcat_get(nid: str) -> float:
    global _offcat_loaded
    if not _offcat_loaded:
        _offcat_loaded = True
        try:
            import json
            with open(_offcat_file(), "r", encoding="utf-8") as f:
                _offcat_attempts.update({str(k): float(v) for k, v in json.load(f).items()})
        except (OSError, ValueError):
            pass
    return _offcat_attempts.get(nid, 0.0)


def _offcat_mark(nids) -> None:
    import json
    now = _time.time()
    for nid in nids:
        _offcat_attempts[nid] = now
    try:
        with open(_offcat_file(), "w", encoding="utf-8") as f:
            json.dump(_offcat_attempts, f)
    except OSError:
        pass


def update_all_module_tles(allow_celestrak: bool = True, only_stale: bool = False) -> dict:
    """Refresh the element sets of every satellite in EVERY module (not just the
    active one) using the configured TLE source.

    Each distinct satellite is resolved once and applied to every module that
    holds it. An element set is only replaced by a strictly newer one, never
    downgraded. With ``only_stale`` satellites whose elements are younger than
    TLE_STALE_SEC are skipped (the background auto-update). Changed modules are
    saved to disk; the active one is updated in place and its propagation
    records rebuilt.

    Returns {"updated": distinct satellites that got newer elements,
    "total": distinct satellites across all modules, "failed": [names],
    "modules": number of modules}.
    """
    from datetime import datetime, timezone
    from backend.config import TLE_STALE_SEC, OFFCATALOG_STALE_SEC, OFFCATALOG_SPACING

    with _tle_update_lock:
        active  = state["active_module"]
        modules = {}
        for name in mod_mgr.list_modules():
            if active and active.get("name") == name:
                modules[name] = active          # live object, shared with state
            else:
                try:
                    modules[name] = mod_mgr.load_module(name)
                except FileNotFoundError:
                    continue

        now = datetime.now(timezone.utc)

        def _stale(sat):
            if not only_stale:
                return True
            try:
                age = (now - tle_sources.tle_epoch_dt(sat.get("tle_line1", ""))).total_seconds()
                return not (0 <= age < TLE_STALE_SEC)
            except Exception:
                return True

        src = state["config"].get("tle_source", "celestrak")
        have_catalog = tle_sources.bulk_info()["count"] > 0

        def _needs_request(nid):
            """True if resolving this satellite would cost a Celestrak request:
            Celestrak is in play and the bulk catalog doesn't hold it."""
            if not (allow_celestrak and have_catalog
                    and src in ("celestrak", "file_then_celestrak")):
                return False
            if tle_sources.bulk_find(nid):
                return False
            if src == "file_then_celestrak":
                try:
                    if tle_sources.find_in_file(state["config"].get("tle_file", ""), nid):
                        return False
                except (FileNotFoundError, ValueError):
                    pass
            return True

        def _offcatalog_stale(sat):
            try:
                age = (now - tle_sources.tle_epoch_dt(sat.get("tle_line1", ""))).total_seconds()
                return not (0 <= age < OFFCATALOG_STALE_SEC)
            except Exception:
                return True

        distinct, wanted, offcat = set(), {}, set()
        for mod in modules.values():
            for sat in mod.get("satellites", []):
                nid = sat.get("norad_id")
                if not nid:
                    continue
                distinct.add(nid)
                if not _stale(sat):
                    continue
                if _needs_request(nid):
                    # Decayed objects have no current elements; derelicts barely
                    # change. Skip the former, rate-limit the latter.
                    st = satcat.cached_status(nid)
                    if ((st and st.get("decayed")) or not _offcatalog_stale(sat)
                            or _time.time() - _offcat_get(nid) < OFFCATALOG_STALE_SEC):
                        continue
                    offcat.add(nid)
                wanted.setdefault(nid, sat.get("name") or nid)

        if offcat:
            _offcat_mark(offcat)          # count the attempt even if Celestrak has nothing
        results, failed, first_req = {}, [], True
        for nid, name in wanted.items():
            if nid in offcat:
                if not first_req:
                    _time.sleep(OFFCATALOG_SPACING)
                first_req = False
            res = tle_sources.resolve_tle(nid, state["config"],
                                          allow_celestrak=allow_celestrak)
            if res:
                results[nid] = res
            else:
                failed.append(name)

        improved, dirty = set(), set()
        for mname, mod in modules.items():
            for sat in mod.get("satellites", []):
                res = results.get(sat.get("norad_id"))
                if not res:
                    continue
                _, l1, l2 = res
                if (sat.get("tle_line1"), sat.get("tle_line2")) == (l1, l2):
                    continue
                try:
                    newer = (tle_sources.tle_epoch_dt(l1)
                             > tle_sources.tle_epoch_dt(sat.get("tle_line1", "")))
                except Exception:
                    newer = True        # existing elements unreadable: take the new ones
                if newer:
                    sat["tle_line1"], sat["tle_line2"] = l1, l2
                    improved.add(sat["norad_id"])
                    dirty.add(mname)

        for mname in dirty:
            mod_mgr.save_module(modules[mname])
        if active and active.get("name") in dirty:
            _reload_records()

        return {"updated": len(improved), "total": len(distinct),
                "failed": failed, "modules": len(modules)}


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
        "offline_mode": netmode.is_offline(),
        "config":  state["config"],
        "celestrak_blocked_until": celestrak.blocked_until(),   # unix ts, 0 if fine
    })


# ── Modules ──────────────────────────────────────────────────

@bp.route("/api/modules", methods=["GET"])
def get_modules():
    modules = mod_mgr.list_modules()
    active  = (state["active_module"] or {}).get("name")
    if active:
        active = mod_mgr.canonical_name(active)     # same spelling the list and the URLs use
    return jsonify({"modules": modules, "active": active})


@bp.route("/api/modules", methods=["POST"])
def create_module():
    name = (request.json or {}).get("name", "").strip()
    if not name:
        return jsonify({"error": "Name is required"}), 400
    try:
        name = mod_mgr.clean_name(name)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
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
    try:
        new_name = mod_mgr.clean_name(new_name)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
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

    # Local copy of the Celestrak catalog (one bulk download, see
    # tle_sources.refresh_bulk): a hit costs no Celestrak request at all. Only a
    # miss (decayed / derelict / unknown) falls through to a single lookup below.
    info = tle_sources.bulk_info()
    if info["count"]:
        hits, total = tle_sources.bulk_search(q)
        if hits:
            return jsonify({"results": hits, "total": total, "catalog": info})

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
    """Re-fetch TLEs for every satellite in EVERY module, using the configured
    TLE source (Celestrak and/or a local file).

    With Celestrak this is one bulk catalog download (``tle_sources.refresh_bulk``)
    followed by a purely local update of all modules, so the request count does
    not grow with the number of satellites. A manual refresh is still gated to
    once per TLE_MANUAL_COOLDOWN_SEC – Celestrak temporarily blocks IPs that
    request more often than the data changes (~2 h). Pass {"force": true} to
    override (the UI asks the user to confirm)."""
    from backend.config import TLE_MANUAL_COOLDOWN_SEC

    force  = bool((request.json or {}).get("force"))
    src    = state["config"].get("tle_source", "celestrak")
    last   = float(state["config"].get("tle_last_refresh", 0) or 0)
    since  = _time.time() - last
    if (not force and src in ("celestrak", "file_then_celestrak")
            and not netmode.is_offline() and since < TLE_MANUAL_COOLDOWN_SEC):
        wait_min = int((TLE_MANUAL_COOLDOWN_SEC - since) / 60) + 1
        return jsonify({
            "cooldown": True,
            "wait_minutes": wait_min,
            "last_refresh_min_ago": int(since / 60),
            "error": (f"Last TLE update was {int(since/60)} min ago. Celestrak "
                      f"rate-limits frequent requests – wait ~{wait_min} min "
                      f"more, or force it anyway."),
        }), 429

    # Re-download the URL sources (a LAN server, say); files / folders are read live.
    sources_res = []
    if src in ("file", "file_then_celestrak"):
        sources_res = tle_sources.refresh_url_sources(state["config"].get("tle_file", ""))

    # One up-front check: if Celestrak can't be reached, skip it (going straight
    # to the local catalogs) instead of eating a long timeout per request.
    allow_ct = (src == "file") or celestrak.celestrak_up()

    # One request refreshes the whole catalog; every module is then updated
    # from that local copy.
    bulk = {"refreshed": False, "count": 0}
    if src in ("celestrak", "file_then_celestrak") and allow_ct:
        bulk = tle_sources.refresh_bulk(force=True)

    resp = update_all_module_tles(allow_celestrak=allow_ct, only_stale=False)

    state["config"]["tle_last_refresh"] = _time.time()
    mod_mgr.save_config(state["config"])

    resp["catalog"] = bulk
    resp["sources"] = sources_res
    note = ""
    if netmode.is_offline():
        note = "Offline station mode – used local sources and data only."
    elif celestrak.is_blocked():
        note = ("Celestrak is rate-limiting this IP – used cached / "
                "SatNOGS elements where possible.")
    elif src in ("celestrak", "file_then_celestrak") and not bulk["refreshed"]:
        note = "Could not download the Celestrak catalog – used cached elements."
    bad = [r for r in sources_res if not r["ok"]]
    if bad:
        note = (note + "  " if note else "") + "TLE source failed: " + "; ".join(
            f"{r['source']} ({r['error']})" for r in bad)
    if note:
        resp["note"] = note
    return jsonify(resp)


@bp.route("/api/tle/sources")
def tle_sources_status():
    """Status of every line of the TLE-sources setting (no download)."""
    return jsonify({"sources": tle_sources.sources_status(state["config"].get("tle_file", ""))})


@bp.route("/api/tle/sources/refresh", methods=["POST"])
def tle_sources_refresh():
    """Re-download the URL lines of the TLE sources and report each source's
    status. Body {spec} lets the Settings dialog check the text it shows before
    it has been saved; without it the saved setting is used."""
    spec = (request.json or {}).get("spec")
    if spec is None:
        spec = state["config"].get("tle_file", "")
    fetched = {r["source"]: r for r in tle_sources.refresh_url_sources(spec)}
    rows = tle_sources.sources_status(spec)
    for row in rows:
        r = fetched.get(row["source"])
        if r is not None:
            row["fetched_ok"] = r["ok"]
            if not r["ok"]:
                row["error"] = r["error"] + (" – using the last copy" if row["count"] else "")
    return jsonify({"sources": rows})


@bp.route("/api/datapack/export")
def export_datapack():
    """The offline data pack (SatNOGS satellites / transmitters / tle) as a ZIP.
    ``?refresh=1`` (default) downloads fresh lists first."""
    import json
    from flask import Response
    refresh = request.args.get("refresh", "1") == "1"
    try:
        blob, manifest = datapack.build_pack(refresh=refresh)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 503
    fname = f"kepler73_datapack_{manifest['created'][:10]}.zip"
    info = {k: manifest[k] for k in ("data_fetched", "refreshed", "counts")}
    info["offline"] = netmode.is_offline()
    return Response(blob, mimetype="application/zip", headers={
        "Content-Disposition": f'attachment; filename="{fname}"',
        "X-Datapack-Info": json.dumps(info),
    })


@bp.route("/api/datapack/import", methods=["POST"])
def import_datapack():
    """Install an offline data pack (ZIP) or one raw SatNOGS API JSON list."""
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "No file uploaded"}), 400
    raw = f.read(datapack.MAX_UPLOAD_BYTES + 1)
    try:
        result = datapack.install(raw)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, **result})


@bp.route("/api/update-check")
def update_check():
    """Is there a newer version on GitHub? (About dialog button; never in
    offline-station mode.) Always answers 200 with {update_available, latest, error, ...}."""
    return jsonify(updatecheck.check())


@bp.route("/api/datapack/info")
def datapack_info():
    """What SatNOGS data is on this computer and how old it is."""
    return jsonify(datapack.info())


@bp.route("/api/tle/catalog")
def tle_catalog_info():
    """Status of the local bulk copy of the Celestrak catalog (what search and
    "Update all TLEs" work from): {count, fetched, age_sec, groups}."""
    return jsonify(tle_sources.bulk_info())



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
    """Transponder list for a satellite: the user's own manual entries first
    (flagged ``manual: true``, kept in data/custom_transponders.json so a SatNOGS
    refresh never touches them), then the cached SatNOGS list."""
    from backend.satnogs import get_transponders
    from backend import userdata
    force = request.args.get("refresh", "0") == "1"
    try:
        satnogs_list = get_transponders(norad_id, force_refresh=force)
    except Exception as e:
        print(f"[Kepler73] transponder lookup failed for {norad_id}: {e}")
        satnogs_list = []
    return jsonify(userdata.custom_tx(norad_id) + satnogs_list)


@bp.route("/api/transmitters/<norad_id>/custom", methods=["POST"])
def save_custom_transmitter(norad_id):
    """Add (or, with ``uuid`` in the body, update) a manual transponder.
    Frequencies are in MHz: downlink_mhz / downlink_high_mhz / uplink_mhz /
    uplink_high_mhz, plus description, mode, baud, invert, active, note."""
    from backend import userdata
    try:
        tx = userdata.save_custom_tx(norad_id, request.json or {})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, "tx": tx})


@bp.route("/api/transmitters/<norad_id>/custom/<tx_uuid>", methods=["DELETE"])
def delete_custom_transmitter(norad_id, tx_uuid):
    from backend import userdata
    if not userdata.delete_custom_tx(norad_id, tx_uuid):
        return jsonify({"error": "Not found"}), 404
    return jsonify({"ok": True})


# ── Satellite notes ("check this signal", "of interest") ────

@bp.route("/api/notes")
def get_notes():
    from backend import userdata
    return jsonify(userdata.get_notes())


@bp.route("/api/notes/<norad_id>", methods=["POST"])
def set_note(norad_id):
    """Body {text, flag}. A blank text removes the note."""
    from backend import userdata
    data = request.json or {}
    note = userdata.set_note(norad_id, data.get("text", ""), bool(data.get("flag")))
    return jsonify({"ok": True, "note": note})


# ── Export / import of everything the user entered ──────────

def _known_names() -> dict:
    names = {}
    for mname in mod_mgr.list_modules():
        try:
            for s in mod_mgr.load_module(mname).get("satellites", []):
                names[str(s.get("norad_id"))] = s.get("name", "")
        except FileNotFoundError:
            continue
    return names


@bp.route("/api/userdata/export")
def export_userdata():
    """Manual transponders + notes as one JSON download, to move to another
    computer (re-import there with POST /api/userdata/import)."""
    import json
    from flask import Response
    from backend import userdata
    doc = userdata.export_all(_known_names())
    return Response(json.dumps(doc, indent=2, ensure_ascii=False),
                    mimetype="application/json",
                    headers={"Content-Disposition":
                             'attachment; filename="kepler73_userdata.json"'})


@bp.route("/api/userdata/import", methods=["POST"])
def import_userdata():
    """Merge an exported user-data file (multipart ``file=`` or a JSON body)."""
    import json
    from backend import userdata
    try:
        if request.files.get("file"):
            raw = request.files["file"].read(2_000_000)
            doc = json.loads(raw.decode("utf-8-sig"))
        else:
            doc = request.get_json(silent=True)
        return jsonify({"ok": True, **userdata.import_all(doc)})
    except (ValueError, UnicodeDecodeError) as e:
        return jsonify({"error": f"Could not import: {e}"}), 400


@bp.route("/api/satnogs/catalog")
def satnogs_catalog():
    """Full SatNOGS catalog (satellites x transmitters) for the lookup dialog.
    ?refresh=1 re-downloads the three source lists."""
    from backend.satnogs import get_catalog
    try:
        return jsonify(get_catalog(force_refresh=request.args.get("refresh") == "1"))
    except Exception as e:
        return jsonify({"error": f"SatNOGS unavailable: {e}"}), 503


# ── Radio / CAT control (Hamlib rigctld) ────────────────────

# app_config key  ->  rig_cfg key
_RIG_CFG_KEYS = {
    "rig_enabled":     "enabled",
    "rig_host":        "host",
    "rig_port":        "port",
    "rig_down_hz":     "down_hz",
    "rig_up_hz":       "up_hz",
    "rig_tx_doppler":  "tx_enabled",
    "rig_deadband_hz": "deadband_hz",
    "rig_bands":       "bands",
}


@bp.route("/api/rig/config", methods=["GET"])
def get_rig_config():
    from api.sockets import rig_cfg, rig_status
    return jsonify({"config": rig_cfg, "status": rig_status})


@bp.route("/api/rig/config", methods=["POST"])
def set_rig_config():
    """Update the live CAT config and mirror it into app_config.json so it
    survives a restart. Body keys match rig_cfg (host, port, down_hz, up_hz,
    tx_enabled, deadband_hz, enabled)."""
    from api.sockets import apply_rig_cfg, rig_cfg
    apply_rig_cfg(request.json or {})
    for cfg_key, rig_key in _RIG_CFG_KEYS.items():
        state["config"][cfg_key] = rig_cfg[rig_key]
    mod_mgr.save_config(state["config"])
    return jsonify({"ok": True, "config": rig_cfg})


@bp.route("/api/rig/test", methods=["POST"])
def test_rig():
    """One-shot connect to a rigctld and read the current frequency. Returns a
    raw ``probe`` so a silent/odd rigctld can be diagnosed from the UI."""
    from backend.hamlib import RigctldClient
    data = request.json or {}
    host = str(data.get("host") or "127.0.0.1").strip()
    try:
        port = int(data.get("port") or 4532)
    except (TypeError, ValueError):
        port = 4532
    c = RigctldClient(host, port, timeout=2.5)
    ok, detail = c.ping()
    probe = c.probe()
    c.close()
    return jsonify({"ok": ok, "detail": detail, "probe": probe})


# ── Rotator control (Hamlib rotctld) ────────────────────────

# app_config key  ->  rot_cfg key
_ROT_CFG_KEYS = {
    "rot_enabled":     "enabled",
    "rot_host":        "host",
    "rot_port":        "port",
    "rot_min_el":      "min_el",
    "rot_park_az":     "park_az",
    "rot_park_el":     "park_el",
    "rot_az_deadband": "az_deadband",
    "rot_el_deadband": "el_deadband",
}


def _persist_rot_cfg(rot_cfg):
    for cfg_key, rot_key in _ROT_CFG_KEYS.items():
        state["config"][cfg_key] = rot_cfg[rot_key]
    mod_mgr.save_config(state["config"])


@bp.route("/api/rotor/config", methods=["GET"])
def get_rotor_config():
    from api.sockets import rot_cfg, rot_status
    return jsonify({"config": rot_cfg, "status": rot_status})


@bp.route("/api/rotor/config", methods=["POST"])
def set_rotor_config():
    """Update the live rotor config and mirror it to app_config.json. Body keys
    match rot_cfg (host, port, min_el, park_az, park_el, az_deadband,
    el_deadband, enabled)."""
    from api.sockets import apply_rot_cfg, rot_cfg
    apply_rot_cfg(request.json or {})
    _persist_rot_cfg(rot_cfg)
    return jsonify({"ok": True, "config": rot_cfg})


@bp.route("/api/rotor/test", methods=["POST"])
def test_rotor():
    from backend.rotctld import RotctldClient
    data = request.json or {}
    host = str(data.get("host") or "127.0.0.1").strip()
    try:
        port = int(data.get("port") or 4533)
    except (TypeError, ValueError):
        port = 4533
    c = RotctldClient(host, port, timeout=2.5)
    ok, detail = c.ping()
    probe = c.probe()
    c.close()
    return jsonify({"ok": ok, "detail": detail, "probe": probe})


@bp.route("/api/rotor/park", methods=["POST"])
def rotor_park():
    """Halt tracking and drive the rotor to the configured park position."""
    from api.sockets import apply_rot_cfg, rot_cfg
    from backend.rotctld import RotctldClient
    apply_rot_cfg({"enabled": False})
    _persist_rot_cfg(rot_cfg)
    c = RotctldClient(rot_cfg["host"], rot_cfg["port"], timeout=2.5)
    ok = c.set_pos(float(rot_cfg["park_az"]), float(rot_cfg["park_el"]))
    err = c.last_error
    c.close()
    return jsonify({"ok": bool(ok), "error": err})


@bp.route("/api/rotor/stop", methods=["POST"])
def rotor_stop():
    """Halt tracking and send the rotator a stop command (leave it where it is)."""
    from api.sockets import apply_rot_cfg, rot_cfg
    from backend.rotctld import RotctldClient
    apply_rot_cfg({"enabled": False})
    _persist_rot_cfg(rot_cfg)
    c = RotctldClient(rot_cfg["host"], rot_cfg["port"], timeout=2.5)
    ok = c.stop()
    err = c.last_error
    c.close()
    return jsonify({"ok": bool(ok), "error": err})


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
    if "offline_mode" in data:
        state["config"]["offline_mode"] = bool(data["offline_mode"])
        netmode.set_offline(state["config"]["offline_mode"])
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
    if netmode.is_offline():
        return None
    try:
        if layer == "satellite":
            url = (f"https://server.arcgisonline.com/ArcGIS/rest/services/"
                   f"World_Imagery/MapServer/tile/{z}/{y}/{x}")
        else:
            url = f"https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        req = urllib.request.Request(
            url, headers={"User-Agent": "Kepler73/0.4 (amateur radio)"})
        with urllib.request.urlopen(req, timeout=6) as r:
            return r.read()
    except Exception:
        return None