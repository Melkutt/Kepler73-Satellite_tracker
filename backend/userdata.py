# -*- coding: utf-8 -*-
"""
Kepler73 - user-entered data: manual transponders and satellite notes.

Both live in their own files (data/custom_transponders.json, data/sat_notes.json),
separate from the SatNOGS transponder cache, so a SatNOGS refresh or a TLE update
can never overwrite them. They can be exported and imported as one file to move
to another computer.
"""

import os
import json
import uuid
import threading
from datetime import datetime, timezone

from backend.config import DATA_DIR

TX_FILE   = os.path.join(DATA_DIR, "custom_transponders.json")
NOTE_FILE = os.path.join(DATA_DIR, "sat_notes.json")
NOTE_MAX  = 1000
IMPORT_MAX_ITEMS = 5000

_lock = threading.Lock()


# ── file helpers ─────────────────────────────────────────────

def _load(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _nid(norad_id) -> str:
    try:
        return str(int(str(norad_id).strip()))
    except (TypeError, ValueError):
        return str(norad_id).strip()


# ── manual transponders ──────────────────────────────────────

def _hz(mhz):
    """MHz (number or string, comma or dot) -> integer Hz, or None if blank."""
    if mhz is None or str(mhz).strip() == "":
        return None
    try:
        value = float(str(mhz).strip().replace(",", "."))
    except ValueError:
        raise ValueError("Frequencies must be numbers in MHz")
    if value <= 0:
        return None
    hz = int(round(value * 1e6))
    if hz >= 10 ** 12:
        raise ValueError("Frequency out of range")
    return hz


def _fmt_range(lo, hi):
    if not lo:
        return None
    if hi and hi != lo:
        return f"{lo / 1e6:.4f} – {hi / 1e6:.4f} MHz"
    return f"{lo / 1e6:.4f} MHz"


def make_custom_tx(payload: dict, keep_uuid: str = None, added: str = None) -> dict:
    """Validate a UI payload and build the stored transponder dict (same keys as
    the parsed SatNOGS ones, plus ``manual: True``). Raises ValueError."""
    desc = str(payload.get("description") or "").strip()
    if not desc:
        raise ValueError("Description is required")

    dl,  dl_hi = _hz(payload.get("downlink_mhz")), _hz(payload.get("downlink_high_mhz"))
    ul,  ul_hi = _hz(payload.get("uplink_mhz")),   _hz(payload.get("uplink_high_mhz"))
    if not dl and not ul:
        raise ValueError("Enter a downlink and/or an uplink frequency")
    if dl_hi and dl and dl_hi < dl:
        dl, dl_hi = dl_hi, dl
    if ul_hi and ul and ul_hi < ul:
        ul, ul_hi = ul_hi, ul

    baud = None
    if str(payload.get("baud") or "").strip():
        try:
            baud = int(float(str(payload["baud"]).replace(",", ".")))
        except ValueError:
            raise ValueError("Baud rate must be a number")

    now = _now()
    return {
        "uuid":             keep_uuid or "custom-" + uuid.uuid4().hex[:12],
        "description":      desc[:120],
        "mode":             str(payload.get("mode") or "").strip()[:24] or "—",
        "uplink":           _fmt_range(ul, ul_hi),
        "downlink":         _fmt_range(dl, dl_hi),
        "downlink_hz":      dl,
        "downlink_high_hz": dl_hi,
        "uplink_hz":        ul,
        "uplink_high_hz":   ul_hi,
        "active":           bool(payload.get("active", True)),
        "status":           "manual",
        "baud":             baud,
        "invert":           bool(payload.get("invert", False)),
        "note":             str(payload.get("note") or "").strip()[:300],
        "manual":           True,
        "added":            added or now,
        "updated":          now,
    }


def _tx_to_payload(tx: dict) -> dict:
    """Stored/exported transponder -> the MHz payload make_custom_tx expects."""
    def mhz(key):
        v = tx.get(key)
        return (v / 1e6) if isinstance(v, (int, float)) and v else None
    return {
        "description":       tx.get("description"),
        "mode":              tx.get("mode") if tx.get("mode") != "—" else "",
        "downlink_mhz":      mhz("downlink_hz"),
        "downlink_high_mhz": mhz("downlink_high_hz"),
        "uplink_mhz":        mhz("uplink_hz"),
        "uplink_high_mhz":   mhz("uplink_high_hz"),
        "baud":              tx.get("baud"),
        "invert":            tx.get("invert", False),
        "active":            tx.get("active", True),
        "note":              tx.get("note", ""),
    }


def custom_tx(norad_id) -> list:
    return list(_load(TX_FILE).get(_nid(norad_id), []))


def save_custom_tx(norad_id, payload: dict) -> dict:
    """Add a manual transponder, or update the one whose uuid is in the payload."""
    nid = _nid(norad_id)
    with _lock:
        data  = _load(TX_FILE)
        items = data.setdefault(nid, [])
        uid   = payload.get("uuid")
        old   = next((t for t in items if t.get("uuid") == uid), None) if uid else None
        tx    = make_custom_tx(payload, keep_uuid=old["uuid"] if old else None,
                               added=old.get("added") if old else None)
        if old:
            items[items.index(old)] = tx
        else:
            items.append(tx)
        _save(TX_FILE, data)
    return tx


def delete_custom_tx(norad_id, tx_uuid: str) -> bool:
    nid = _nid(norad_id)
    with _lock:
        data  = _load(TX_FILE)
        items = data.get(nid, [])
        kept  = [t for t in items if t.get("uuid") != tx_uuid]
        if len(kept) == len(items):
            return False
        if kept:
            data[nid] = kept
        else:
            data.pop(nid, None)
        _save(TX_FILE, data)
    return True


# ── satellite notes ──────────────────────────────────────────

def get_notes() -> dict:
    return _load(NOTE_FILE)


def set_note(norad_id, text: str, flag: bool = False):
    """Store (or, if ``text`` is blank, remove) the note for a satellite.
    Returns the stored note dict, or None when it was removed."""
    nid  = _nid(norad_id)
    text = (text or "").strip()[:NOTE_MAX]
    with _lock:
        data = _load(NOTE_FILE)
        if not text:
            data.pop(nid, None)
            _save(NOTE_FILE, data)
            return None
        data[nid] = {"text": text, "flag": bool(flag), "updated": _now()}
        _save(NOTE_FILE, data)
        return data[nid]


# ── export / import ──────────────────────────────────────────

def export_all(names: dict = None) -> dict:
    """Everything the user entered, as one portable document. ``names`` maps
    norad_id -> satellite name purely so the file is readable by a human."""
    tx    = {k: v for k, v in _load(TX_FILE).items() if v}
    notes = _load(NOTE_FILE)
    names = names or {}
    return {
        "kepler73_userdata": 1,
        "exported":          _now(),
        "satellite_names":   {n: names[n] for n in set(tx) | set(notes) if n in names},
        "transponders":      tx,
        "notes":             notes,
    }


def import_all(doc) -> dict:
    """Merge an exported document into the local data. For an entry present on
    both sides the one with the later ``updated`` stamp wins; nothing is deleted.
    Every transponder is re-validated. Raises ValueError for a foreign file."""
    if not isinstance(doc, dict) or doc.get("kepler73_userdata") != 1:
        raise ValueError("Not a Kepler73 user-data file")
    tx_in = doc.get("transponders") or {}
    notes_in = doc.get("notes") or {}
    if not isinstance(tx_in, dict) or not isinstance(notes_in, dict):
        raise ValueError("Malformed user-data file")
    if sum(len(v) for v in tx_in.values() if isinstance(v, list)) + len(notes_in) > IMPORT_MAX_ITEMS:
        raise ValueError("File holds too many entries")

    added = updated = notes_n = skipped = 0
    with _lock:
        tx_all = _load(TX_FILE)
        for nid, items in tx_in.items():
            if not isinstance(items, list):
                continue
            cur = tx_all.setdefault(_nid(nid), [])
            for t in items:
                if not isinstance(t, dict):
                    skipped += 1
                    continue
                uid = t.get("uuid") if str(t.get("uuid", "")).startswith("custom-") else None
                try:
                    clean = make_custom_tx(_tx_to_payload(t), keep_uuid=uid,
                                           added=t.get("added"))
                except ValueError:
                    skipped += 1
                    continue
                clean["updated"] = str(t.get("updated") or clean["updated"])
                old = next((x for x in cur if x.get("uuid") == clean["uuid"]), None)
                if old is None:
                    cur.append(clean)
                    added += 1
                elif clean["updated"] > str(old.get("updated", "")):
                    cur[cur.index(old)] = clean
                    updated += 1
        tx_all = {k: v for k, v in tx_all.items() if v}
        _save(TX_FILE, tx_all)

        notes_all = _load(NOTE_FILE)
        for nid, n in notes_in.items():
            if not isinstance(n, dict) or not str(n.get("text", "")).strip():
                skipped += 1
                continue
            cur = notes_all.get(_nid(nid))
            incoming = {"text": str(n["text"]).strip()[:NOTE_MAX],
                        "flag": bool(n.get("flag")),
                        "updated": str(n.get("updated") or _now())}
            if cur is None or incoming["updated"] > str(cur.get("updated", "")):
                notes_all[_nid(nid)] = incoming
                notes_n += 1
        _save(NOTE_FILE, notes_all)

    return {"transponders_added": added, "transponders_updated": updated,
            "notes_imported": notes_n, "skipped": skipped}
