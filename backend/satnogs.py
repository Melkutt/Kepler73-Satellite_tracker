# -*- coding: utf-8 -*-
"""
Kepler73 - satnogs.py
Fetches transponder data from SatNOGS API and caches locally.
API endpoint: https://db.satnogs.org/api/transmitters/?format=json&norad_cat_id=<id>
"""

import os
import json
import time
import urllib.request
from backend.config import DATA_DIR, NETWORK_TIMEOUT

SATNOGS_URL  = "https://db.satnogs.org/api/transmitters/"
TRANSPONDER_DIR = os.path.join(DATA_DIR, "transponders")
CACHE_MAX_AGE   = 7 * 86400   # 7 days in seconds

os.makedirs(TRANSPONDER_DIR, exist_ok=True)


def _cache_path(norad_id: str) -> str:
    return os.path.join(TRANSPONDER_DIR, f"{norad_id}.json")


def _cache_age(norad_id: str) -> float:
    """Returns age of cache file in seconds, or infinity if not cached."""
    path = _cache_path(norad_id)
    if not os.path.exists(path):
        return float('inf')
    return time.time() - os.path.getmtime(path)


def _fetch_from_api(norad_id: str) -> list:
    """Fetch transponder list from SatNOGS API."""
    url = f"{SATNOGS_URL}?format=json&satellite__norad_cat_id={norad_id}"
    req = urllib.request.Request(url, headers={"User-Agent": "Kepler73/0.3"})
    with urllib.request.urlopen(req, timeout=NETWORK_TIMEOUT) as resp:
        return json.loads(resp.read().decode())


def _parse_transponders(raw: list) -> list:
    """Parse SatNOGS API response into clean transponder dicts."""
    result = []
    for t in raw:
        # Only include entries that have at least a downlink frequency
        if not t.get("downlink_low"):
            continue

        uplink_low   = t.get("uplink_low")
        uplink_high  = t.get("uplink_high")
        downlink_low = t.get("downlink_low")
        downlink_high= t.get("downlink_high")

        # Format frequency as MHz string
        def fmt_freq(f):
            if not f:
                return None
            mhz = f / 1e6
            return f"{mhz:.4f} MHz"

        def fmt_range(lo, hi):
            if not lo:
                return None
            if hi and hi != lo:
                return f"{lo/1e6:.4f} – {hi/1e6:.4f} MHz"
            return f"{lo/1e6:.4f} MHz"

        result.append({
            "uuid":        t.get("uuid", ""),
            "description": t.get("description") or t.get("type", "Unknown"),
            "mode":        t.get("mode") or "—",
            "uplink":      fmt_range(uplink_low, uplink_high),
            "downlink":    fmt_range(downlink_low, downlink_high),
            "downlink_hz": downlink_low,
            "uplink_hz":   uplink_low,
            "active":      bool(t.get("alive", False) or t.get("status") == "active"),
            "status":      t.get("status", "unknown"),
            "baud":        t.get("baud"),
            "invert":      bool(t.get("invert", False)),
        })

    # Sort: active first, then by description
    result.sort(key=lambda x: (not x["active"], x["description"].lower()))
    return result


def get_transponders(norad_id: str, force_refresh: bool = False) -> list:
    """
    Returns list of transponders for a satellite.
    Uses local cache if available and fresh enough.
    Returns empty list if offline or no data found.
    """
    cache = _cache_path(norad_id)

    # Return cached data if fresh
    if not force_refresh and _cache_age(norad_id) < CACHE_MAX_AGE:
        try:
            with open(cache, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    # Fetch from API
    try:
        raw  = _fetch_from_api(norad_id)
        data = _parse_transponders(raw)
        with open(cache, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return data
    except Exception as e:
        print(f"[Kepler73] SatNOGS fetch failed for {norad_id}: {e}")
        # Return stale cache if available
        if os.path.exists(cache):
            try:
                with open(cache, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return []


def cache_age_days(norad_id: str) -> float:
    """Returns cache age in days, or -1 if not cached."""
    age = _cache_age(norad_id)
    if age == float('inf'):
        return -1
    return round(age / 86400, 1)


# ===========================================================================
# SatNOGS full-catalog lookup (searchable satellite list with filters)
# Ported from the standalone "Sat Toolkit" SatNOGS Lookup tab.
# ===========================================================================

from datetime import datetime, timezone

from sgp4.api import Satrec
from backend.sgp4_engine import propagate, now_jd

API_BASE        = "https://db.satnogs.org/api"
SATNOGS_DB_DIR  = os.path.join(DATA_DIR, "satnogs_db")
DB_CACHE_MAX_AGE = 7 * 86400
_GEO_MM_MIN, _GEO_MM_MAX = 0.90, 1.10   # mean motion band for "geostationary"

os.makedirs(SATNOGS_DB_DIR, exist_ok=True)


def _db_path(name: str) -> str:
    return os.path.join(SATNOGS_DB_DIR, name)


def _fetch_db_list(endpoint: str, refresh: bool = False) -> list:
    """Fetch a full SatNOGS list endpoint (satellites / transmitters / tle),
    cached to disk for DB_CACHE_MAX_AGE. Falls back to a stale cache on error."""
    path = _db_path(f"{endpoint}.json")
    if (not refresh and os.path.exists(path)
            and time.time() - os.path.getmtime(path) < DB_CACHE_MAX_AGE):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    url = f"{API_BASE}/{endpoint}/?format=json"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Kepler73/0.3"})
        with urllib.request.urlopen(req, timeout=90) as r:
            data = json.loads(r.read())
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        return data
    except Exception:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        raise


def _mean_motion(tle2: str):
    if not tle2 or len(tle2) < 63:
        return None
    try:
        return float(tle2[52:63])
    except ValueError:
        return None


def _freq_mhz(hz):
    return round(hz / 1_000_000, 6) if hz else None


def _build_catalog(refresh: bool = False) -> dict:
    satellites   = _fetch_db_list("satellites", refresh)
    transmitters = _fetch_db_list("transmitters", refresh)
    tles         = _fetch_db_list("tle", refresh)

    geo = {}
    tle_map = {}
    for t in tles:
        n = t.get("norad_cat_id")
        if n is None:
            continue
        if t.get("tle1") and t.get("tle2"):
            tle_map[n] = [t["tle1"], t["tle2"]]
        mm = _mean_motion(t.get("tle2", ""))
        if mm is not None:
            geo[n] = "Yes" if _GEO_MM_MIN <= mm <= _GEO_MM_MAX else "No"

    # Sub-satellite longitude for each geostationary object, so the frontend
    # can work out its elevation from the observer's location.
    geo_lon = {}
    jd = now_jd()
    for n, (l1, l2) in tle_map.items():
        if geo.get(n) != "Yes":
            continue
        try:
            res = propagate(Satrec.twoline2rv(l1, l2), jd)
            if res:
                geo_lon[n] = round(res[1], 3)   # res = (lat, lon, alt, ...)
        except Exception:
            pass

    sat_by_norad = {s.get("norad_cat_id"): s for s in satellites
                    if s.get("norad_cat_id") is not None}

    rows = []
    seen_norads = set()
    for tx in transmitters:
        n = tx.get("norad_cat_id")
        seen_norads.add(n)
        sat = sat_by_norad.get(n)
        base = {
            "norad":   n,
            "name":    (sat.get("name") if sat else None) or tx.get("sat_id") or "Unknown",
            "country": (sat.get("countries") if sat else None) or "Unknown",
            "geo":     geo.get(n, "?"),
            "geo_lon": geo_lon.get(n),
            "mode":    tx.get("mode") or "Unknown",
            "service": tx.get("service") or "Unknown",
            "type":    tx.get("type") or "Unknown",
            "desc":    tx.get("description") or "",
            "txst":    tx.get("status") or ("active" if tx.get("alive") else "inactive"),
            "satst":   (sat.get("status") if sat else None) or "unknown",
        }
        added = False
        for d, lo, hi in (("Downlink", "downlink_low", "downlink_high"),
                          ("Uplink",   "uplink_low",   "uplink_high")):
            f = tx.get(lo) or tx.get(hi)
            if not f:
                continue
            added = True
            rows.append({**base, "dir": d, "mhz": _freq_mhz(f)})
        if not added:
            rows.append({**base, "dir": "Unknown", "mhz": None})

    for n, sat in sat_by_norad.items():
        if n in seen_norads:
            continue
        rows.append({
            "norad": n, "name": sat.get("name") or "Unknown",
            "country": sat.get("countries") or "Unknown",
            "geo": geo.get(n, "?"), "geo_lon": geo_lon.get(n),
            "dir": "Unknown", "mhz": None,
            "mode": "Unknown", "service": "Unknown", "type": "Unknown",
            "desc": "No known transmitter", "txst": "unknown",
            "satst": sat.get("status") or "unknown",
        })

    return {
        "rows": rows,
        "tles": {str(k): v for k, v in tle_map.items()},
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "counts": {"rows": len(rows),
                   "satellites": len(set(r["norad"] for r in rows))},
    }


_tle_map_cache: tuple = (0.0, {})   # (catalog.json mtime, {norad: [l1, l2]})


def _catalog_tle_map() -> dict:
    """The catalog's norad -> [l1, l2] map, memoised by catalog.json mtime."""
    path = _db_path("catalog.json")
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    if _tle_map_cache[0] != mtime:
        try:
            with open(path, "r", encoding="utf-8") as f:
                globals()["_tle_map_cache"] = (mtime, json.load(f).get("tles", {}))
        except Exception:
            globals()["_tle_map_cache"] = (mtime, {})
    return _tle_map_cache[1]


def catalog_tle(norad_id):
    """(line1, line2) for a satellite from the cached SatNOGS catalog, or
    None. Reads catalog.json only – never hits the network or rebuilds."""
    v = _catalog_tle_map().get(str(norad_id).strip())
    return (v[0], v[1]) if v and len(v) >= 2 else None


def get_catalog(force_refresh: bool = False) -> dict:
    """Return the built, filterable SatNOGS catalog. The assembled result is
    cached to catalog.json and only rebuilt when a source list is newer."""
    cat_path = _db_path("catalog.json")
    srcs = [_db_path(f"{e}.json") for e in ("satellites", "transmitters", "tle")]

    if not force_refresh and os.path.exists(cat_path):
        cat_m = os.path.getmtime(cat_path)
        if all(os.path.exists(s) and os.path.getmtime(s) <= cat_m for s in srcs):
            try:
                with open(cat_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass

    catalog = _build_catalog(refresh=force_refresh)
    try:
        with open(cat_path, "w", encoding="utf-8") as f:
            json.dump(catalog, f)
    except Exception:
        pass
    return catalog