# -*- coding: utf-8 -*-
"""
Kepler73 – Local TLE / GP element sources

Parsers for the formats Celestrak publishes (see
https://celestrak.org/NORAD/documentation/gp-data-formats.php):

  * classic TLE / 3LE  (.txt, .tle)   – Alpha-5 catalog numbers supported
  * GP JSON / OMM       (.json)        – array of OMM objects
  * GP CSV              (.csv)         – header row of OMM field names

Every parser returns the same canonical record shape used elsewhere in the
app (``celestrak.search_tle`` output, ``POST /api/modules/<m>/satellites`` input)::

    {"name", "norad_id", "tle_line1", "tle_line2", "tle_epoch"}

A file path may point at a single file or at a folder; a folder loads every
*.txt / *.tle / *.json / *.csv inside it.
"""

import io
import os
import csv
import json
import glob
from datetime import datetime, timedelta, timezone

from sgp4.api import Satrec
from sgp4 import omm
from sgp4.exporter import export_tle

# Alpha-5 alphabet: digits then A-Z without I and O (Celestrak scheme).
_ALPHA5 = "0123456789ABCDEFGHJKLMNPQRSTUVWXYZ"

_FILE_GLOBS = ("*.txt", "*.tle", "*.json", "*.csv")

# path -> (signature, [records]) cache so repeated searches don't re-read disk
_catalog_cache: dict = {}


# ── Catalog-number helpers ───────────────────────────────────

def alpha5_to_num(field: str) -> str:
    """Decode a TLE catalog-number field to its plain numeric string.

    ``"25544"`` -> ``"25544"``  and  ``"A0912"`` -> ``"100912"``.
    Returns the stripped input unchanged if it cannot be decoded.
    """
    s = (field or "").strip()
    if not s:
        return s
    head = s[0].upper()
    if head.isalpha():
        try:
            return str(_ALPHA5.index(head) * 10000 + int(s[1:]))
        except ValueError:
            return s
    try:
        return str(int(s))
    except ValueError:
        return s


def tle_epoch_dt(line1: str) -> datetime:
    """Parse the epoch (columns 19-32, ``YYDDD.DDDDDDDD``) of a TLE line 1
    into a timezone-aware UTC datetime."""
    raw = line1[18:32].strip()
    yy = int(raw[:2])
    doy = float(raw[2:])
    year = 2000 + yy if yy < 57 else 1900 + yy
    return (datetime(year, 1, 1, tzinfo=timezone.utc)
            + timedelta(days=doy - 1.0))


# ── Individual format parsers ───────────────────────────────

def parse_tle_text(text: str) -> list:
    """Parse classic 2-line and 3-line element sets."""
    records = []
    pending_name = ""
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]

    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("1 ") and i + 1 < len(lines) and lines[i + 1].startswith("2 "):
            l1, l2 = ln, lines[i + 1]
            try:
                Satrec.twoline2rv(l1, l2)
            except Exception:
                i += 1
                continue
            norad = alpha5_to_num(l1[2:7])
            records.append({
                "name":      pending_name or f"NORAD {norad}",
                "norad_id":  norad,
                "tle_line1": l1,
                "tle_line2": l2,
                "tle_epoch": l1[18:32].strip(),
            })
            pending_name = ""
            i += 2
        elif ln.startswith("2 "):
            i += 1
        else:
            # A name line (Celestrak 3LE may prefix it with "0 ").
            pending_name = ln[2:].strip() if ln.startswith("0 ") else ln.strip()
            i += 1

    return records


def _omm_fields_to_record(fields: dict) -> dict | None:
    """Turn one OMM field dict into a canonical record via Satrec."""
    try:
        f = {k.upper(): v for k, v in fields.items()}
        epoch = f.get("EPOCH", "")
        if epoch and "." not in epoch:
            f["EPOCH"] = epoch + ".000000"
        sat = Satrec()
        omm.initialize(sat, f)
        l1, l2 = export_tle(sat)
        norad = alpha5_to_num(str(f.get("NORAD_CAT_ID", l1[2:7])))
        return {
            "name":      f.get("OBJECT_NAME") or f"NORAD {norad}",
            "norad_id":  norad,
            "tle_line1": l1,
            "tle_line2": l2,
            "tle_epoch": (f.get("EPOCH", "") or "")[:10],
        }
    except Exception:
        return None


def parse_gp_json(text: str) -> list:
    """Parse a Celestrak GP/OMM JSON document (array or single object)."""
    data = json.loads(text)
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []
    out = []
    for entry in data:
        rec = _omm_fields_to_record(entry)
        if rec:
            out.append(rec)
    return out


def parse_gp_csv(text: str) -> list:
    """Parse a Celestrak GP CSV document (header row of OMM field names)."""
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        rec = _omm_fields_to_record(row)
        if rec:
            out.append(rec)
    return out


def parse_any(text: str) -> list:
    """Sniff the format of ``text`` and dispatch to the right parser."""
    stripped = text.lstrip()
    if not stripped:
        return []

    if stripped[0] in "[{":
        return parse_gp_json(text)

    first_line = stripped.splitlines()[0].upper()
    if "," in first_line and ("NORAD_CAT_ID" in first_line or "OBJECT_NAME" in first_line
                              or "MEAN_MOTION" in first_line):
        return parse_gp_csv(text)

    if any(ln.startswith(("1 ", "2 ", "0 ")) for ln in stripped.splitlines()):
        return parse_tle_text(text)

    # Last resort: try each parser in turn.
    for parser in (parse_gp_json, parse_gp_csv, parse_tle_text):
        try:
            recs = parser(text)
            if recs:
                return recs
        except Exception:
            continue
    return []


# ── File / folder catalog ───────────────────────────────────

def _iter_paths(path: str):
    if os.path.isdir(path):
        seen = set()
        for pat in _FILE_GLOBS:
            for p in sorted(glob.glob(os.path.join(path, pat))):
                if p not in seen:
                    seen.add(p)
                    yield p
    elif os.path.isfile(path):
        yield path


def _signature(path: str):
    return tuple((p, os.path.getmtime(p)) for p in _iter_paths(path))


def load_file_records(path: str) -> list:
    """Load and cache *every* record from a TLE/GP file or folder, keeping
    duplicates (e.g. the same satellite at several epochs across dated files).

    Raises FileNotFoundError if the path does not exist and ValueError if
    nothing could be parsed.
    """
    path = os.path.expanduser((path or "").strip())
    if not path or not os.path.exists(path):
        raise FileNotFoundError(f"TLE path does not exist: {path or '(empty)'}")

    sig = _signature(path)
    cached = _catalog_cache.get(path)
    if cached and cached[0] == sig:
        return cached[1]

    records: list = []
    for p in _iter_paths(path):
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        records.extend(parse_any(text))

    if not records:
        raise ValueError(f"No TLE / GP data found in: {path}")

    _catalog_cache[path] = (sig, records)
    return records


def load_file_catalog(path: str) -> list:
    """One record per satellite (latest file wins on a duplicate norad_id)."""
    by_norad: dict = {}
    for rec in load_file_records(path):
        by_norad[rec["norad_id"]] = rec
    return list(by_norad.values())


def search_file(path: str, query: str) -> list:
    """Search a local catalog by NORAD id (exact) or name (substring)."""
    records = load_file_catalog(path)
    q = (query or "").strip()
    if not q:
        return records
    if q.isdigit():
        wanted = str(int(q))
        return [r for r in records if r["norad_id"] == wanted]
    ql = q.lower()
    return [r for r in records if ql in r["name"].lower()]


def _wanted_id(norad_id) -> str:
    try:
        return str(int(str(norad_id).strip()))
    except (TypeError, ValueError):
        return str(norad_id)


def find_in_file(path: str, norad_id: str) -> dict | None:
    """Return the single (latest) record matching ``norad_id``."""
    wanted = _wanted_id(norad_id)
    for rec in load_file_catalog(path):
        if rec["norad_id"] == wanted:
            return rec
    return None


def find_in_file_near(path: str, norad_id: str, when: datetime) -> dict | None:
    """Return the record for ``norad_id`` whose TLE epoch is closest to
    ``when`` (a timezone-aware datetime). Lets the pass simulator use a
    period-appropriate element set from a folder of dated TLE files.
    """
    wanted = _wanted_id(norad_id)
    best, best_dist = None, None
    for rec in load_file_records(path):
        if rec["norad_id"] != wanted:
            continue
        try:
            ep = tle_epoch_dt(rec["tle_line1"])
        except Exception:
            continue
        dist = abs((ep - when).total_seconds())
        if best_dist is None or dist < best_dist:
            best, best_dist = rec, dist
    return best


# ── Source-aware resolution (shared by routes.py and sockets.py) ──

def _satnogs_fallback(norad_id: str) -> tuple | None:
    """Element set from the cached SatNOGS catalog – used as a fallback when
    Celestrak is unreachable or rate-limiting us."""
    try:
        from backend.satnogs import catalog_tle
        hit = catalog_tle(norad_id)
        if hit:
            return (f"NORAD {norad_id}", hit[0], hit[1])
    except Exception:
        pass
    return None


def _newest(*candidates) -> tuple | None:
    """Of several (name, line1, line2) tuples, return the one with the newest
    TLE epoch. Lets a fresh SatNOGS element set win over a stale Celestrak
    disk-cache hit when Celestrak itself is blocked."""
    best, best_ep = None, None
    for c in candidates:
        if not c:
            continue
        try:
            ep = tle_epoch_dt(c[1])
        except Exception:
            ep = None
        if best is None or (ep is not None and (best_ep is None or ep > best_ep)):
            best, best_ep = c, ep
    return best


def resolve_tle(norad_id: str, config: dict, allow_celestrak: bool = True) -> tuple | None:
    """Return (name, line1, line2) for a satellite, honouring the configured
    TLE source: ``celestrak`` (default), ``file`` (offline only), or
    ``file_then_celestrak``. When Celestrak yields nothing (or ``allow_celestrak``
    is False because a quick reachability check failed), falls back to the cached
    SatNOGS catalog so a blocked / offline Celestrak doesn't leave a satellite
    stuck on a months-old element set. With Celestrak the freshest epoch wins.
    """
    cfg = config or {}
    source = cfg.get("tle_source", "celestrak")
    path = cfg.get("tle_file", "")

    if source in ("file", "file_then_celestrak") and path:
        try:
            rec = find_in_file(path, norad_id)
        except (FileNotFoundError, ValueError):
            rec = None
        if rec:
            return rec["name"], rec["tle_line1"], rec["tle_line2"]
        if source == "file":
            return _satnogs_fallback(norad_id)

    if source == "file" and not path:
        return _satnogs_fallback(norad_id)

    if not allow_celestrak:
        return _satnogs_fallback(norad_id)

    from backend import celestrak
    return _newest(celestrak.fetch_tle(norad_id), _satnogs_fallback(norad_id))
