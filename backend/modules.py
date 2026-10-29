# -*- coding: utf-8 -*-
"""
Kepler73 – Module management
Each module is stored as a JSON file in data/modules/
"""

import os
import json
from datetime import datetime, timezone
from backend.config import (MODULES_DIR, SAT_COLORS, APP_CONFIG,
                            TLE_SOURCE, TLE_FILE)


_BAD_NAME_CHARS = '/\\:*?"<>|'


def clean_name(name: str) -> str:
    """A module name that is safe as a file name and survives a round trip through
    ``list_modules()``: path-hostile characters become '-'. Raises ValueError if
    nothing usable is left."""
    cleaned = "".join("-" if c in _BAD_NAME_CHARS else c for c in (name or "")).strip()
    if not cleaned.strip("-. "):
        raise ValueError("The module name needs at least one letter or digit")
    return cleaned


def _path(name: str) -> str:
    safe = name.replace(" ", "_").replace("/", "-")
    return os.path.join(MODULES_DIR, f"{safe}.json")


def canonical_name(name: str) -> str:
    """The name ``list_modules()`` shows for this module (derived from its file
    name). A module saved under a name with a '/' in it, for example, is listed
    with a '-'; the UI must use the listed form in URLs."""
    return os.path.basename(_path(name))[:-5].replace("_", " ")


def list_modules() -> list:
    return [f[:-5].replace("_", " ")
            for f in sorted(os.listdir(MODULES_DIR))
            if f.endswith(".json")]


def load_module(name: str) -> dict:
    path = _path(name)
    if not os.path.exists(path):
        raise FileNotFoundError(f"Module '{name}' does not exist")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_module(module: dict):
    with open(_path(module["name"]), "w", encoding="utf-8") as f:
        json.dump(module, f, indent=2, ensure_ascii=False)


def create_module(name: str) -> dict:
    mod = {
        "name": name,
        "created": datetime.now(timezone.utc).isoformat(),
        "satellites": []
    }
    save_module(mod)
    return mod


def delete_module(name: str):
    path = _path(name)
    if os.path.exists(path):
        os.remove(path)


def rename_module(old: str, new: str) -> dict:
    mod = load_module(old)
    delete_module(old)
    mod["name"] = new
    save_module(mod)
    return mod


def add_satellite(module: dict, sat: dict) -> dict:
    sat.setdefault("color", _next_color(module))
    sat.setdefault("alarm_min", 5)
    sat.setdefault("alarm_enabled", False)   # opt-in: some satellites matter more than others
    sat["added"] = datetime.now(timezone.utc).isoformat()
    # Remove duplicates
    module["satellites"] = [
        s for s in module["satellites"]
        if s.get("norad_id") != sat.get("norad_id")
    ]
    module["satellites"].append(sat)
    save_module(module)
    return module


def remove_satellite(module: dict, norad_id: str) -> dict:
    module["satellites"] = [
        s for s in module["satellites"]
        if s.get("norad_id") != norad_id
    ]
    save_module(module)
    return module


def update_tle(module: dict, norad_id: str,
               l1: str, l2: str, epoch: str) -> dict:
    for sat in module["satellites"]:
        if sat.get("norad_id") == norad_id:
            sat["tle_line1"]   = l1
            sat["tle_line2"]   = l2
            sat["tle_epoch"]   = epoch
            sat["tle_updated"] = datetime.now(timezone.utc).isoformat()
    save_module(module)
    return module


def _next_color(module: dict) -> str:
    used = {s.get("color") for s in module.get("satellites", [])}
    for c in SAT_COLORS:
        if c not in used:
            return c
    return SAT_COLORS[len(module["satellites"]) % len(SAT_COLORS)]


# ── App configuration ───────────────────────────────────────

def load_config() -> dict:
    defaults = {
        "lat": 59.3293,
        "lon": 18.0686,
        "alt": 20.0,
        "min_el": 5.0,
        "active_module": None,
        "tle_source": TLE_SOURCE,
        "tle_file": TLE_FILE,
        "tle_last_refresh": 0,   # unix ts of the last manual "Update all TLEs"
        "offline_mode": False,   # never contact public internet services
        # Radio / CAT (Hamlib rigctld) Doppler tuning
        "rig_enabled": False,
        "rig_host": "127.0.0.1",
        "rig_port": 4532,
        "rig_down_hz": 0.0,
        "rig_up_hz": 0.0,
        "rig_tx_doppler": False,
        "rig_deadband_hz": 10.0,
        "rig_bands": "",
        # Az/El rotator (Hamlib rotctld)
        "rot_enabled": False,
        "rot_host": "127.0.0.1",
        "rot_port": 4533,
        "rot_min_el": 3.0,
        "rot_park_az": 0.0,
        "rot_park_el": 0.0,
        "rot_az_deadband": 2.0,
        "rot_el_deadband": 2.0,
    }
    if os.path.exists(APP_CONFIG):
        try:
            with open(APP_CONFIG, "r") as f:
                defaults.update(json.load(f))
        except Exception:
            pass
    return defaults


def save_config(cfg: dict):
    with open(APP_CONFIG, "w") as f:
        json.dump(cfg, f, indent=2)