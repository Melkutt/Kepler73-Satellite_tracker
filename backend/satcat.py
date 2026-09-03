# -*- coding: utf-8 -*-
"""
Kepler73 - satcat.py
Decay / status lookups from the Celestrak SATCAT, with a local disk cache.

get_status(norad_id) -> {"norad_id", "name", "decayed", "decay_date"}
  "decayed" is True / False, or None when the status is unknown (offline and
  never cached). Decay status effectively never changes back, so the cache
  TTL is long.
"""

import os
import json
import time
import threading

from backend.config import DATA_DIR
from backend.celestrak import lookup_satcat

SATCAT_DIR    = os.path.join(DATA_DIR, "satcat")
CACHE_MAX_AGE = 30 * 86400   # 30 days

os.makedirs(SATCAT_DIR, exist_ok=True)

_mem: dict = {}          # norad_id -> (timestamp, data|None)  – short in-process cache
_last_attempt: dict = {}  # norad_id -> timestamp of last network attempt
_ATTEMPT_COOLDOWN = 300   # don't re-hit the network for the same id within 5 min


def _cache_path(norad_id: str) -> str:
    return os.path.join(SATCAT_DIR, f"{norad_id}.json")


def _read_cache(norad_id: str):
    path = _cache_path(norad_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def get_status(norad_id: str, force_refresh: bool = False) -> dict:
    """Return decay status for a catalog number, fetching + caching if stale."""
    nid = str(norad_id).strip()
    path = _cache_path(nid)

    if (not force_refresh and os.path.exists(path)
            and time.time() - os.path.getmtime(path) < CACHE_MAX_AGE):
        cached = _read_cache(nid)
        if cached is not None:
            _mem[nid] = (time.time(), cached)
            return cached

    rec = lookup_satcat(nid)          # network call
    if rec is not None:
        decay = rec.get("DECAY_DATE")
        data = {
            "norad_id":   str(rec.get("NORAD_CAT_ID") or nid),
            "name":       rec.get("OBJECT_NAME") or "",
            "decayed":    bool(decay),
            "decay_date": decay or None,
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception:
            pass
        _mem[nid] = (time.time(), data)
        return data

    # Offline / unknown: fall back to any stale cache, else "unknown".
    stale = _read_cache(nid)
    if stale is not None:
        return stale
    return {"norad_id": nid, "name": "", "decayed": None, "decay_date": None}


def cached_status(norad_id: str):
    """Cache-only status (no network). Returns the dict, or None if not cached.

    Backed by a 60 s in-process cache so it is cheap to call every broadcast tick.
    """
    nid = str(norad_id).strip()
    now = time.time()
    hit = _mem.get(nid)
    if hit and now - hit[0] < 60:
        return hit[1]
    data = _read_cache(nid)
    _mem[nid] = (now, data)
    return data


def refresh_async(norad_id: str):
    """Fetch + cache the status in a background thread, at most once per
    _ATTEMPT_COOLDOWN per catalog number. Never blocks the caller."""
    nid = str(norad_id).strip()
    now = time.time()
    if now - _last_attempt.get(nid, 0) < _ATTEMPT_COOLDOWN:
        return
    _last_attempt[nid] = now
    threading.Thread(target=get_status, args=(nid,), daemon=True).start()
