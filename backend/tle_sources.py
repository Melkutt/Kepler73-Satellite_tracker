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
import time
import hashlib
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from sgp4.api import Satrec
from sgp4 import omm
from sgp4.exporter import export_tle

from backend.config import BULK_GROUPS, BULK_REFRESH_SEC, TLE_DIR

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


def _satnogs_tle_record(entry: dict) -> dict | None:
    """A SatNOGS DB /api/tle/ entry ({tle0, tle1, tle2, norad_cat_id, ...}) as a
    canonical record."""
    l1 = (entry.get("tle1") or "").strip()
    l2 = (entry.get("tle2") or "").strip()
    if not (l1.startswith("1 ") and l2.startswith("2 ")):
        return None
    try:
        Satrec.twoline2rv(l1, l2)
    except Exception:
        return None
    norad = alpha5_to_num(str(entry.get("norad_cat_id") or l1[2:7]))
    name = (entry.get("tle0") or "").strip()
    if name.startswith("0 "):
        name = name[2:].strip()
    return {
        "name":      name or f"NORAD {norad}",
        "norad_id":  norad,
        "tle_line1": l1,
        "tle_line2": l2,
        "tle_epoch": l1[18:32].strip(),
    }


def parse_gp_json(text: str) -> list:
    """Parse a Celestrak GP/OMM JSON document (array or single object)."""
    data = json.loads(text)
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []
    out = []
    for entry in data:
        if isinstance(entry, dict) and "tle1" in entry:
            rec = _satnogs_tle_record(entry)            # SatNOGS's own TLE JSON
        else:
            rec = _omm_fields_to_record(entry)          # Celestrak GP / OMM JSON
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


# ── TLE sources: several files / folders / URLs, one per line ─
#
# The ``tle_file`` setting holds one source per line (like Gpredict's list of
# TLE servers): a file, a folder (also a network share) or an http(s):// URL,
# e.g. a web server on the LAN. URL sources are downloaded on an explicit
# refresh only (never while searching) and cached as parsed records under
# data/tle_cache/sources/, so they keep working while the server is down.
# These are the user's own sources: the offline-station switch does not block
# them.

SOURCES_DIR   = os.path.join(TLE_DIR, "sources")
URL_MAX_BYTES = 30_000_000
URL_TIMEOUT   = 20
_url_mem: dict = {}      # url -> (cache-file mtime, [records])


def split_sources(spec) -> list:
    """The non-empty, non-comment lines of the TLE-sources setting."""
    out = []
    for ln in str(spec or "").replace("\r", "\n").split("\n"):
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            out.append(ln)
    return out


def _is_url(src: str) -> bool:
    return src.lower().startswith(("http://", "https://"))


def _url_cache_file(url: str) -> str:
    return os.path.join(SOURCES_DIR, hashlib.sha1(url.encode("utf-8")).hexdigest()[:16] + ".json")


def _url_records(url: str) -> list:
    path = _url_cache_file(url)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        raise FileNotFoundError(f"not fetched yet: {url}")
    hit = _url_mem.get(url)
    if hit and hit[0] == mtime:
        return hit[1]
    with open(path, "r", encoding="utf-8") as f:
        records = json.load(f).get("records", [])
    _url_mem[url] = (mtime, records)
    return records


def fetch_url_source(url: str, timeout: float = URL_TIMEOUT) -> dict:
    """Download one URL source (TLE / 3LE text, GP JSON, GP CSV or SatNOGS TLE
    JSON) and cache its records. A failed download keeps the previous cached copy.
    Returns {source, kind, ok, count, error}."""
    res = {"source": url, "kind": "url", "ok": False, "count": 0, "error": ""}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Kepler73/0.4"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(URL_MAX_BYTES + 1)
        if len(raw) > URL_MAX_BYTES:
            raise ValueError("the file is larger than 30 MB")
        records = parse_any(raw.decode("utf-8-sig", "replace"))
        if not records:
            raise ValueError("no element sets found in the download")
        os.makedirs(SOURCES_DIR, exist_ok=True)
        path = _url_cache_file(url)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"url": url, "fetched": time.time(), "records": records}, f)
        os.replace(tmp, path)
        res.update(ok=True, count=len({r["norad_id"] for r in records}))
    except urllib.error.HTTPError as e:
        res["error"] = f"HTTP {e.code} {e.reason}"
    except urllib.error.URLError as e:
        res["error"] = str(e.reason)
    except Exception as e:                      # timeout, bad data, ...
        res["error"] = str(e) or type(e).__name__
    return res


def refresh_url_sources(spec) -> list:
    """Re-download every URL line of ``spec``; returns one result per URL."""
    return [fetch_url_source(s) for s in split_sources(spec) if _is_url(s)]


def sources_status(spec) -> list:
    """One row per line of ``spec`` for the Settings dialog:
    {source, kind: url|file|folder, count (distinct satellites), age_sec, error}."""
    rows = []
    for src in split_sources(spec):
        row = {"source": src, "kind": "url", "count": 0, "age_sec": None, "error": ""}
        try:
            if _is_url(src):
                recs = _url_records(src)
                row["age_sec"] = int(time.time() - os.path.getmtime(_url_cache_file(src)))
            else:
                path = os.path.expanduser(src)
                row["kind"] = "folder" if os.path.isdir(path) else "file"
                recs = _load_path_records(src)
            row["count"] = len({r["norad_id"] for r in recs})
        except FileNotFoundError as e:
            row["error"] = str(e).replace("TLE path does not exist: ", "path not found: ")
        except ValueError as e:
            row["error"] = str(e)
        except Exception as e:
            row["error"] = str(e) or type(e).__name__
        rows.append(row)
    return rows


def load_file_records(spec: str) -> list:
    """Every record from the TLE sources in ``spec`` (one per line: file, folder
    or URL), duplicates kept. A single plain path behaves exactly as before.
    Unusable sources are skipped; raises FileNotFoundError / ValueError only
    when nothing at all could be read."""
    sources = split_sources(spec)
    if not sources:
        raise FileNotFoundError("TLE path does not exist: (empty)")
    if len(sources) == 1 and not _is_url(sources[0]):
        return _load_path_records(sources[0])

    records, problems = [], []
    for src in sources:
        try:
            records.extend(_url_records(src) if _is_url(src) else _load_path_records(src))
        except (FileNotFoundError, ValueError) as e:
            problems.append(f"{src}: {e}")
    if not records:
        raise ValueError("No TLE / GP data from any source – " + "; ".join(problems))
    return records


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


def _load_path_records(path: str) -> list:
    """Load and cache *every* record from one TLE/GP file or folder, keeping
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


def _epoch_ts(rec: dict) -> float:
    try:
        return tle_epoch_dt(rec["tle_line1"]).timestamp()
    except Exception:
        return 0.0


def load_file_catalog(path: str) -> list:
    """One record per satellite: the newest element set wins when several
    sources / files hold the same norad_id (on equal epochs the later one wins)."""
    by_norad: dict = {}
    for rec in load_file_records(path):
        cur = by_norad.get(rec["norad_id"])
        if cur is None or _epoch_ts(rec) >= _epoch_ts(cur):
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


# ── Bulk catalog (whole Celestrak groups, one request each) ──
#
# celestrak.fetch_group() mirrors e.g. GROUP=active into
# data/tle_cache/bulk_<group>.json. Everything below reads that file, so name
# searches and "Update all TLEs" cost zero Celestrak requests once it is fresh.

_bulk_state = {"sig": None, "by_norad": {}, "records": [], "fetched": 0.0, "files": 0}


def _load_bulk() -> dict:
    """The parsed bulk catalog, re-read only when a bulk file's mtime changes.
    The returned dict is replaced wholesale (never mutated) so concurrent
    readers always see a consistent snapshot."""
    global _bulk_state
    from backend import celestrak
    files = [p for p in (celestrak.bulk_path(g) for g in BULK_GROUPS)
             if os.path.exists(p)]
    sig = tuple((p, os.path.getmtime(p)) for p in files)
    if sig == _bulk_state["sig"]:
        return _bulk_state

    by_norad: dict = {}
    oldest = None
    for p in files:
        try:
            with open(p, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            continue
        t = float(doc.get("fetched", 0) or 0)
        oldest = t if oldest is None else min(oldest, t)
        for rec in doc.get("records", []):
            by_norad[rec["norad_id"]] = rec
    _bulk_state = {"sig": sig, "by_norad": by_norad,
                   "records": list(by_norad.values()),
                   "fetched": oldest or 0.0, "files": len(files)}
    return _bulk_state


def bulk_age_sec() -> float:
    """Seconds since the stalest bulk group was downloaded; inf if any
    configured group is missing."""
    st = _load_bulk()
    if not st["by_norad"] or st["files"] < len(BULK_GROUPS):
        return float("inf")
    return max(0.0, time.time() - st["fetched"])


def bulk_info() -> dict:
    st = _load_bulk()
    age = bulk_age_sec()
    return {"count": len(st["by_norad"]),
            "fetched": st["fetched"],
            "age_sec": None if age == float("inf") else int(age),
            "groups": list(BULK_GROUPS)}


def bulk_find(norad_id) -> dict | None:
    return _load_bulk()["by_norad"].get(_wanted_id(norad_id))


def bulk_search(query: str, limit: int = 300) -> tuple:
    """Search the bulk catalog by NORAD id (exact) or name (substring, prefix
    matches first). Returns (records[:limit], total_matches)."""
    recs = _load_bulk()["records"]
    q = (query or "").strip()
    if not q:
        return [], 0
    if q.isdigit():
        wanted = str(int(q))
        hits = [r for r in recs if r["norad_id"] == wanted]
    else:
        ql = q.lower()
        hits = [r for r in recs if ql in r["name"].lower()]
        hits.sort(key=lambda r: (not r["name"].lower().startswith(ql),
                                 r["name"].lower()))
    return hits[:limit], len(hits)


def refresh_bulk(force: bool = False) -> dict:
    """Download the configured Celestrak groups (one request per group) unless
    the cached copy is younger than BULK_REFRESH_SEC and ``force`` is False.
    Returns {"refreshed": bool, "count": satellites in the catalog afterwards}."""
    from backend import celestrak
    if not force and bulk_age_sec() < BULK_REFRESH_SEC:
        return {"refreshed": False, "count": len(_load_bulk()["by_norad"])}
    ok = False
    for g in BULK_GROUPS:
        if celestrak.fetch_group(g):
            ok = True
    return {"refreshed": ok, "count": len(_load_bulk()["by_norad"])}


# ── Source-aware resolution (shared by routes.py and sockets.py) ──

def _bulk_tuple(norad_id: str) -> tuple | None:
    rec = bulk_find(norad_id)
    return (rec["name"], rec["tle_line1"], rec["tle_line2"]) if rec else None


def _local_fallback(norad_id: str) -> tuple | None:
    """Best element set available without touching the network: the bulk
    Celestrak catalog or the cached SatNOGS catalog, whichever is newer."""
    return _newest(_bulk_tuple(norad_id), _satnogs_fallback(norad_id))


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
            return _local_fallback(norad_id)

    if source == "file" and not path:
        return _local_fallback(norad_id)

    if not allow_celestrak:
        return _local_fallback(norad_id)

    from backend import celestrak
    bulk = _bulk_tuple(norad_id)
    if bulk and bulk_age_sec() < BULK_REFRESH_SEC:
        # The whole-catalog download is recent: no per-satellite request needed.
        return _newest(bulk, _satnogs_fallback(norad_id))
    # Not in the bulk catalog (decayed / outside the mirrored groups) or the
    # catalog is stale: ask Celestrak for this one satellite.
    return _newest(bulk, celestrak.fetch_tle(norad_id), _satnogs_fallback(norad_id))
