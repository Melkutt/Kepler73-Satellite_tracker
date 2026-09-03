# -*- coding: utf-8 -*-
"""
Kepler73 - geocode.py
Place-name search (OpenStreetMap Nominatim) and ground elevation
(Open-Meteo elevation API), both proxied and cached on disk so the
location picker degrades gracefully when offline.
"""

import os
import re
import json
import time
import urllib.parse
import urllib.request

from backend.config import DATA_DIR, NETWORK_TIMEOUT

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"

GEOCODE_DIR   = os.path.join(DATA_DIR, "geocode")
CACHE_MAX_AGE = 180 * 86400   # geography does not move

os.makedirs(GEOCODE_DIR, exist_ok=True)

_UA = {"User-Agent": "Kepler73/0.3 (amateur radio satellite tracker)"}


def _cache_get(key: str):
    path = os.path.join(GEOCODE_DIR, key + ".json")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < CACHE_MAX_AGE:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return None


def _cache_put(key: str, data):
    try:
        with open(os.path.join(GEOCODE_DIR, key + ".json"), "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.strip().lower())[:60] or "_"


def geocode(query: str) -> list:
    """Return up to 5 {name, lat, lon} matches for a place name.

    Raises ConnectionError when offline with nothing cached.
    """
    q = (query or "").strip()
    if not q:
        return []

    key = "q_" + _slug(q)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    params = urllib.parse.urlencode({"q": q, "format": "json", "limit": 5,
                                     "addressdetails": 0})
    try:
        req = urllib.request.Request(f"{NOMINATIM_URL}?{params}", headers=_UA)
        with urllib.request.urlopen(req, timeout=NETWORK_TIMEOUT) as r:
            raw = json.loads(r.read())
    except Exception as e:
        stale = _cache_get(key)
        if stale is not None:
            return stale
        raise ConnectionError(f"Geocoding unavailable: {e}")

    out = []
    for row in raw:
        try:
            out.append({
                "name": row.get("display_name", q),
                "lat":  float(row["lat"]),
                "lon":  float(row["lon"]),
            })
        except (KeyError, ValueError):
            continue
    _cache_put(key, out)
    return out


def elevation(lat: float, lon: float) -> float | None:
    """Ground elevation in metres above sea level for lat/lon, or None."""
    key = "e_%.4f_%.4f" % (round(lat, 4), round(lon, 4))
    cached = _cache_get(key)
    if cached is not None:
        return cached.get("elevation")

    params = urllib.parse.urlencode({"latitude": round(lat, 5),
                                     "longitude": round(lon, 5)})
    try:
        req = urllib.request.Request(f"{ELEVATION_URL}?{params}", headers=_UA)
        with urllib.request.urlopen(req, timeout=NETWORK_TIMEOUT) as r:
            data = json.loads(r.read())
        elev = float(data["elevation"][0])
    except Exception:
        stale = _cache_get(key)
        return stale.get("elevation") if stale is not None else None

    _cache_put(key, {"elevation": elev})
    return elev
