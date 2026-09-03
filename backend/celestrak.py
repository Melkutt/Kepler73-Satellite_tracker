# -*- coding: utf-8 -*-
"""
Kepler73 – Celestrak TLE/OMM fetching with local cache
Always tries JSON/OMM first (supports NORAD IDs 100000+),
falls back to TLE format for compatibility.
"""

import os
import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from sgp4.api import Satrec
from sgp4 import omm
from backend.config import (CELESTRAK_URL, NETWORK_TIMEOUT, TLE_DIR,
                            CELESTRAK_BACKOFF_SEC)

SATCAT_URL = "https://celestrak.org/satcat/records.php"

# ── Rate-limit back-off ──────────────────────────────────────
# Celestrak temporarily blocks IPs that request per-satellite data too often.
# When we detect a block we stop hitting Celestrak entirely for a while so we
# don't make it worse; the UI surfaces `blocked_until()`.
_blocked_until = 0.0


def is_blocked() -> bool:
    return time.time() < _blocked_until


def blocked_until() -> float:
    """Unix timestamp the back-off ends, or 0 if not blocked."""
    return _blocked_until if is_blocked() else 0.0


def _note_block(reason: str = ""):
    global _blocked_until
    _blocked_until = time.time() + CELESTRAK_BACKOFF_SEC
    print(f"[Kepler73] Celestrak rate-limit detected{(' – ' + reason) if reason else ''}; "
          f"backing off for {CELESTRAK_BACKOFF_SEC // 3600} h")


def _looks_blocked(exc: Exception = None, body: str = "") -> bool:
    if isinstance(exc, urllib.error.HTTPError) and exc.code in (403, 429):
        return True
    low = (body or "").lower()
    return any(s in low for s in ("you have been blocked", "rate limit",
                                  "too many requests", "exceeded", "abuse"))


def _cache_path(norad_id: str, fmt: str = 'json') -> str:
    return os.path.join(TLE_DIR, f"{norad_id}.{fmt}")


def fetch_tle(norad_id: str) -> tuple | None:
    """
    Fetch satellite data from Celestrak.
    Tries JSON/OMM first, then TLE as a fallback.
    Returns (name, line1, line2) for backward compatibility,
    or None on failure.
    """
    # 1. Try JSON/OMM
    result = _fetch_json(norad_id)
    if result:
        return result

    # 2. Fallback: TLE format (only works for NORAD IDs < 70000)
    result = _fetch_tle_legacy(norad_id)
    if result:
        return result

    # 3. Try local cache
    return load_cached_tle(norad_id)


def _fetch_json(norad_id: str) -> tuple | None:
    """Fetch OMM/JSON from Celestrak and save it locally."""
    if is_blocked():
        return None
    url = f"{CELESTRAK_URL}?CATNR={norad_id}&FORMAT=JSON"
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Kepler73/0.3"})
        with urllib.request.urlopen(req, timeout=NETWORK_TIMEOUT) as r:
            raw = r.read()

        if _looks_blocked(body=raw[:2000].decode("utf-8", "replace")):
            _note_block("gp.php body")
            return None

        data = json.loads(raw)
        if not data or not isinstance(data, list):
            return None

        omm_data = data[0]

        # Save JSON cache
        with open(_cache_path(norad_id, 'json'), 'w') as f:
            json.dump(omm_data, f)

        return _omm_to_tuple(omm_data)

    except Exception as e:
        if _looks_blocked(exc=e):
            _note_block(f"HTTP {getattr(e, 'code', '?')}")
        return None


def _fetch_tle_legacy(norad_id: str) -> tuple | None:
    """Fetch classic TLE format (fallback for older satellites)."""
    if is_blocked():
        return None
    url = f"{CELESTRAK_URL}?CATNR={norad_id}&FORMAT=TLE"
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Kepler73/0.3"})
        with urllib.request.urlopen(req, timeout=NETWORK_TIMEOUT) as r:
            text = r.read().decode("utf-8")

        if _looks_blocked(body=text):
            _note_block("gp.php body")
            return None

        lines = [l.strip() for l in text.splitlines() if l.strip()]
        if len(lines) >= 3 and lines[1].startswith("1 "):
            # Save TLE cache
            with open(_cache_path(norad_id, 'tle'), 'w') as f:
                f.write("\n".join(lines[:3]))
            return lines[0], lines[1], lines[2]

    except Exception as e:
        if _looks_blocked(exc=e):
            _note_block(f"HTTP {getattr(e, 'code', '?')}")
        return None

    return None


def _omm_to_tuple(omm_data: dict) -> tuple | None:
    """Convert an OMM dict to (name, line1, line2) via Satrec."""
    try:
        name = omm_data.get("OBJECT_NAME", "UNKNOWN")
        sat = Satrec()
        omm.initialize(sat, omm_data)
        # Generate TLE strings from Satrec for backward compatibility
        from sgp4.exporter import export_tle
        line1, line2 = export_tle(sat)
        return name, line1, line2
    except Exception:
        return None


def load_cached_tle(norad_id: str) -> tuple | None:
    """Load from local cache – tries JSON first, then TLE."""
    # Try JSON cache
    json_path = _cache_path(norad_id, 'json')
    if os.path.exists(json_path):
        try:
            with open(json_path, 'r') as f:
                omm_data = json.load(f)
            return _omm_to_tuple(omm_data)
        except Exception:
            pass

    # Try TLE cache
    tle_path = _cache_path(norad_id, 'tle')
    if os.path.exists(tle_path):
        try:
            with open(tle_path, 'r') as f:
                lines = [l.strip() for l in f if l.strip()]
            if len(lines) >= 3:
                return lines[0], lines[1], lines[2]
        except Exception:
            pass

    return None


def get_satrec(norad_id: str) -> Satrec | None:
    """
    Return a Satrec straight from the OMM cache when possible.
    Faster than fetch_tle + twoline2rv.
    """
    json_path = _cache_path(norad_id, 'json')
    if os.path.exists(json_path):
        try:
            with open(json_path, 'r') as f:
                omm_data = json.load(f)
            sat = Satrec()
            omm.initialize(sat, omm_data)
            return sat
        except Exception:
            pass
    return None


def search_tle(query: str) -> list:
    """
    Search Celestrak by name or NORAD ID.
    Returns a list of {name, norad_id, tle_line1, tle_line2, tle_epoch}
    """
    if is_blocked():
        import time as _t
        mins = int((blocked_until() - _t.time()) / 60) + 1
        raise ConnectionError(
            f"Celestrak has rate-limited this IP – backing off (~{mins} min left). "
            f"Use a local TLE file, or try later.")

    if query.isdigit():
        url = f"{CELESTRAK_URL}?CATNR={query}&FORMAT=JSON"
    else:
        url = f"{CELESTRAK_URL}?NAME={query.replace(' ', '+')}&FORMAT=JSON"

    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Kepler73/0.3"})
        with urllib.request.urlopen(req, timeout=NETWORK_TIMEOUT) as r:
            data = json.loads(r.read())

        if not data or not isinstance(data, list):
            return []

        results = []
        for omm_data in data:
            try:
                name     = omm_data.get("OBJECT_NAME", "UNKNOWN")
                norad_id = str(omm_data.get("NORAD_CAT_ID", ""))
                epoch    = omm_data.get("EPOCH", "")[:10]

                sat = Satrec()
                omm.initialize(sat, omm_data)
                from sgp4.exporter import export_tle
                line1, line2 = export_tle(sat)

                results.append({
                    "name":      name,
                    "norad_id":  norad_id,
                    "tle_line1": line1,
                    "tle_line2": line2,
                    "tle_epoch": epoch,
                })
            except Exception:
                continue

        return results

    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise ConnectionError(
                f"No current elements on Celestrak for '{query}' "
                f"(unknown catalog number or decayed object)")
        if _looks_blocked(exc=e):
            _note_block(f"HTTP {e.code} on search")
            raise ConnectionError("Celestrak has rate-limited this IP – backing off.")
        raise ConnectionError(f"Celestrak HTTP {e.code}: {e.reason}")
    except Exception as e:
        raise ConnectionError(f"Celestrak error: {e}")


def lookup_satcat(norad_id: str) -> dict | None:
    """Fetch the Celestrak SATCAT record for a catalog number.

    Returns the record dict (keys OBJECT_NAME, NORAD_CAT_ID, DECAY_DATE,
    OPS_STATUS_CODE, LAUNCH_DATE, ...) or None if unknown / offline.
    `DECAY_DATE` is null for an object still in orbit, else "YYYY-MM-DD".
    """
    if not str(norad_id).strip().isdigit() or is_blocked():
        return None
    url = f"{SATCAT_URL}?CATNR={norad_id}&FORMAT=JSON"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Kepler73/0.3"})
        with urllib.request.urlopen(req, timeout=NETWORK_TIMEOUT) as r:
            data = json.loads(r.read())
        if isinstance(data, list) and data:
            return data[0]
        if isinstance(data, dict):
            return data
    except Exception as e:
        if _looks_blocked(exc=e):
            _note_block(f"HTTP {getattr(e, 'code', '?')} on satcat")
    return None


def check_online() -> bool:
    """Check internet connectivity using a reliable host."""
    for url in ["https://google.com", "https://cloudflare.com", "https://celestrak.org"]:
        try:
            urllib.request.urlopen(url, timeout=3)
            return True
        except Exception:
            continue
    return False


def celestrak_up() -> bool:
    """Quick, dedicated check that celestrak.org itself is reachable and not
    rate-limiting us. Used to skip TLE auto-update entirely when Celestrak is
    down (each fetch_tle attempt otherwise burns ~20 s of connection timeouts)."""
    if is_blocked():
        return False
    try:
        urllib.request.urlopen("https://celestrak.org", timeout=4)
        return True
    except Exception as e:
        if _looks_blocked(exc=e):
            _note_block("HEAD celestrak.org")
        return False


def tle_age_days(epoch_str: str) -> int | None:
    """Return how many days have passed since the TLE epoch."""
    try:
        ep = datetime.fromisoformat(epoch_str.replace("Z", "+00:00"))
        if ep.tzinfo is None:
            ep = ep.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ep).days
    except Exception:
        return None