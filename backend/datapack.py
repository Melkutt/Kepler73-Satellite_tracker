# -*- coding: utf-8 -*-
"""
Kepler73 - offline data pack for a station that never sees the internet.

Export (on a computer with internet): the three SatNOGS lists the app is built
on - satellites, transmitters and TLE - plus a manifest, in one small ZIP.

Import (on the offline station): that ZIP, or a single raw SatNOGS API JSON file
saved from db.satnogs.org/api/..., is validated and installed into
data/satnogs_db/. The SatNOGS lookup and every satellite's transponder list then
work with no network. Element sets are NOT the point of the pack - bring those
through the TLE sources (files / network share / LAN server).
"""

import io
import os
import json
import time
import zipfile
from datetime import datetime, timezone

from backend import satnogs, netmode
from backend.config import APP_VERSION

PACK_FORMAT      = 1
KINDS            = ("satellites", "transmitters", "tle")
MAX_MEMBER_BYTES = 120_000_000        # per list, uncompressed
MAX_UPLOAD_BYTES = 150_000_000
_INFO_FILE       = "pack_info.json"
_count_memo: dict = {}


# ── helpers ──────────────────────────────────────────────────

def _path(kind: str) -> str:
    return os.path.join(satnogs.SATNOGS_DB_DIR, f"{kind}.json")


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def _mtime(kind: str) -> float:
    try:
        return os.path.getmtime(_path(kind))
    except OSError:
        return 0.0


def _write_json_atomic(path: str, data) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


# ── export ───────────────────────────────────────────────────

def build_pack(refresh: bool = True) -> tuple:
    """Build the data-pack ZIP. With ``refresh`` the three lists are downloaded
    first (unless the offline switch is on); if that fails the pack holds the
    cached copies and ``manifest["refreshed"]`` is False. Returns
    (zip_bytes, manifest). Raises RuntimeError when there is no data at all."""
    before = {k: _mtime(k) for k in KINDS}
    lists  = {k: satnogs._fetch_db_list(k, refresh=refresh) for k in KINDS}
    after  = {k: _mtime(k) for k in KINDS}

    refreshed = (bool(refresh) and not netmode.is_offline()
                 and all(after[k] > before[k] for k in KINDS))
    manifest = {
        "kepler73_datapack": PACK_FORMAT,
        "created":      datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "app_version":  APP_VERSION,
        "source":       "db.satnogs.org",
        "data_fetched": _iso(min(after.values())),
        "refreshed":    refreshed,
        "counts":       {k: len(lists[k]) for k in KINDS},
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=2))
        for k in KINDS:
            z.writestr(f"satnogs/{k}.json", json.dumps(lists[k], separators=(",", ":")))
    return buf.getvalue(), manifest


# ── import ───────────────────────────────────────────────────

def _detect_kind(items: list):
    """Which SatNOGS list is this raw JSON? (transmitters / satellites / tle)"""
    sample = [x for x in items[:50] if isinstance(x, dict)]
    if not sample:
        return None

    def share(pred):
        return sum(1 for x in sample if pred(x)) / len(sample)

    if share(lambda x: "tle1" in x and "tle2" in x) > 0.8:
        return "tle"
    if share(lambda x: "downlink_low" in x or "uplink_low" in x
             or ("uuid" in x and "alive" in x)) > 0.8:
        return "transmitters"
    if share(lambda x: "sat_id" in x and "name" in x and "status" in x) > 0.8:
        return "satellites"
    return None


def _check_list(kind: str, items) -> list:
    if not isinstance(items, list) or not items:
        raise ValueError(f"The '{kind}' list is empty or not a list")
    sample = items[:200]
    if not all(isinstance(x, dict) for x in sample):
        raise ValueError(f"The '{kind}' list does not look like SatNOGS data")
    if sum(1 for x in sample if "norad_cat_id" in x) < 0.9 * len(sample):
        raise ValueError(f"The '{kind}' list does not look like SatNOGS data "
                         f"(no norad_cat_id)")
    return items


def _read_zip(raw: bytes) -> tuple:
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise ValueError("The file is not a valid ZIP archive")
    with z:
        names = set(z.namelist())
        if "manifest.json" not in names:
            raise ValueError("Not a Kepler73 data pack (no manifest.json)")
        try:
            manifest = json.loads(z.read("manifest.json").decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ValueError("The data pack's manifest is unreadable")
        if not isinstance(manifest, dict) or manifest.get("kepler73_datapack") != PACK_FORMAT:
            raise ValueError("Not a Kepler73 data pack, or a newer format than "
                             "this version understands")
        lists = {}
        for k in KINDS:
            member = f"satnogs/{k}.json"
            if member not in names:
                continue
            if z.getinfo(member).file_size > MAX_MEMBER_BYTES:
                raise ValueError(f"'{k}' is larger than the allowed size")
            with z.open(member) as f:
                data = f.read(MAX_MEMBER_BYTES + 1)      # never trust the header size
            if len(data) > MAX_MEMBER_BYTES:
                raise ValueError(f"'{k}' is larger than the allowed size")
            try:
                parsed = json.loads(data.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                raise ValueError(f"'{k}' is not valid JSON")
            lists[k] = _check_list(k, parsed)
    if not lists:
        raise ValueError("The data pack contains no SatNOGS lists")
    return lists, manifest


def _read_raw_json(raw: bytes) -> tuple:
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError):
        raise ValueError("The file is neither a Kepler73 data pack (ZIP) nor JSON")
    if isinstance(data, dict) and isinstance(data.get("results"), list):
        data = data["results"]                          # a paginated API answer
    if not isinstance(data, list):
        raise ValueError("Expected a SatNOGS JSON list (satellites, transmitters or tle)")
    kind = _detect_kind(data)
    if not kind:
        raise ValueError("Could not tell which SatNOGS list this is – expected the output "
                         "of db.satnogs.org/api/satellites, /transmitters or /tle "
                         "(?format=json)")
    return {kind: _check_list(kind, data)}, None


def install(raw: bytes) -> dict:
    """Validate and install a data pack (ZIP) or a single raw SatNOGS JSON list.
    Raises ValueError with a readable message for anything that is not one."""
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError("The file is too large")
    if zipfile.is_zipfile(io.BytesIO(raw)):
        lists, manifest = _read_zip(raw)
        fmt = "pack"
    else:
        lists, manifest = _read_raw_json(raw)
        fmt = "file"

    os.makedirs(satnogs.SATNOGS_DB_DIR, exist_ok=True)
    for kind, items in lists.items():
        _write_json_atomic(_path(kind), items)           # new mtime = installed now

    # Derived data must follow the new lists.
    try:
        os.remove(os.path.join(satnogs.SATNOGS_DB_DIR, "catalog.json"))
    except OSError:
        pass
    if "transmitters" in lists:                          # per-satellite caches are older
        try:
            for fn in os.listdir(satnogs.TRANSPONDER_DIR):
                if fn.endswith(".json"):
                    os.remove(os.path.join(satnogs.TRANSPONDER_DIR, fn))
        except OSError:
            pass

    counts = {k: len(v) for k, v in lists.items()}
    result = {
        "format":    fmt,
        "installed": list(lists),
        "counts":    counts,
        "created":   (manifest or {}).get("created"),
        "data_fetched": (manifest or {}).get("data_fetched"),
        "catalog":   None,
        "note":      "",
    }
    try:
        result["catalog"] = satnogs.get_catalog(network=False)["counts"]   # rebuild, no network
    except Exception:
        result["note"] = ("Installed. The SatNOGS lookup also needs the satellites and "
                          "tle lists – import the full data pack to enable it.")

    _write_json_atomic(os.path.join(satnogs.SATNOGS_DB_DIR, _INFO_FILE), {
        "installed": time.time(), "format": fmt, "created": result["created"],
        "data_fetched": result["data_fetched"], "counts": counts,
    })
    return result


# ── status ───────────────────────────────────────────────────

def _count(kind: str) -> int:
    p = _path(kind)
    m = os.path.getmtime(p)
    hit = _count_memo.get(kind)
    if hit and hit[0] == m:
        return hit[1]
    try:
        with open(p, "r", encoding="utf-8") as f:
            n = len(json.load(f))
    except (OSError, ValueError):
        n = 0
    _count_memo[kind] = (m, n)
    return n


def info() -> dict:
    """What SatNOGS data is on this computer and how old it is."""
    out = {"lists": {}, "data_date": None, "pack": None,
           "offline_mode": netmode.is_offline()}
    mtimes = []
    for k in KINDS:
        if os.path.exists(_path(k)):
            out["lists"][k] = _count(k)
            mtimes.append(_mtime(k))
    if not mtimes:
        return out
    out["data_date"] = _iso(min(mtimes))
    try:
        with open(os.path.join(satnogs.SATNOGS_DB_DIR, _INFO_FILE), "r", encoding="utf-8") as f:
            pack = json.load(f)
        # Valid only while the lists are the ones that pack installed.
        if max(mtimes) <= float(pack.get("installed", 0)) + 5:
            out["pack"] = pack
            if pack.get("data_fetched"):
                out["data_date"] = pack["data_fetched"]
    except (OSError, ValueError):
        pass
    return out
