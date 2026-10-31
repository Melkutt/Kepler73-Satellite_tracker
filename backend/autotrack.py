# -*- coding: utf-8 -*-
"""
Kepler73 - auto-track
Pick satellites from any of your modules; for every upcoming pass whose MAXIMUM elevation
reaches a threshold the app selects the satellite, steers the rotor / CAT Doppler and runs
your own commands (typically a script that starts / stops an SDR recording).

This file holds the pure logic (plan + state machine + command runner). The side effects
on the live app (module switch, rotor, rig, socket events) are in api/sockets.py.
"""

import os
import re
import shlex
import subprocess
import threading
import time
import traceback

from sgp4.api import Satrec

import backend.modules as mod_mgr
import backend.satcat as satcat
from backend.config import DATA_DIR
from backend.sgp4_engine import find_next_pass, jd_to_dt, now_jd

HORIZON_H        = 24         # how far ahead the plan looks
LOOKBACK_MIN     = 30         # plan starts this long ago so a pass already in progress is kept
REPLAN_SEC       = 300        # rebuild the plan at least this often (new TLEs, drift)
SAME_PASS_DAYS   = 20 / 1440  # two passes of one satellite closer than this are the same pass

DEFAULTS = {
    "enabled":        False,
    "default_min_el": 20.0,    # a pass counts if its max elevation reaches this
    "lead_sec":       30,      # start this long before AOS
    "tail_sec":       30,      # ... and end this long after LOS
    "use_rotor":      True,    # switch rotor tracking on for the pass
    "use_cat":        True,    # tune CAT Doppler (only for entries with a downlink)
    "max_tle_age_days": 30,    # ignore satellites whose element set is older (0 = never ignore)
    "cmd_start":      "",      # run at the start of a pass
    "cmd_end":        "",      # run at the end of a pass
    "entries":        [],      # [{norad_id, name, module, min_el, down_mhz, enabled}]
}

_ID_OK   = re.compile(r"^[A-Za-z0-9_-]{1,16}$")
_SAFE    = re.compile(r"[^A-Za-z0-9._-]+")


# ── Config ───────────────────────────────────────────────────

def _num(v, lo, hi, default):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return min(hi, max(lo, f))


def _opt_num(v, lo, hi):
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if lo <= f <= hi else None


def normalize(raw: dict) -> dict:
    """A clean config dict (defaults filled in, every value validated)."""
    raw = raw if isinstance(raw, dict) else {}
    cfg = dict(DEFAULTS)
    cfg["enabled"]        = bool(raw.get("enabled", DEFAULTS["enabled"]))
    cfg["default_min_el"] = _num(raw.get("default_min_el"), 0.0, 90.0, DEFAULTS["default_min_el"])
    cfg["lead_sec"]       = int(_num(raw.get("lead_sec"), 0, 3600, DEFAULTS["lead_sec"]))
    cfg["tail_sec"]       = int(_num(raw.get("tail_sec"), 0, 3600, DEFAULTS["tail_sec"]))
    cfg["use_rotor"]      = bool(raw.get("use_rotor", DEFAULTS["use_rotor"]))
    cfg["use_cat"]        = bool(raw.get("use_cat", DEFAULTS["use_cat"]))
    cfg["max_tle_age_days"] = int(_num(raw.get("max_tle_age_days"), 0, 3650, DEFAULTS["max_tle_age_days"]))
    cfg["cmd_start"]      = str(raw.get("cmd_start", "") or "").strip()[:500]
    cfg["cmd_end"]        = str(raw.get("cmd_end", "") or "").strip()[:500]

    entries, seen = [], set()
    for e in raw.get("entries") or []:
        if not isinstance(e, dict):
            continue
        nid = str(e.get("norad_id", "")).strip()
        if not _ID_OK.match(nid) or nid in seen:
            continue
        seen.add(nid)
        entries.append({
            "norad_id": nid,
            "name":     str(e.get("name", "") or "")[:80],
            "module":   str(e.get("module", "") or "")[:120],
            "min_el":   _opt_num(e.get("min_el"), 0.0, 90.0),         # None = use the default
            "down_mhz": _opt_num(e.get("down_mhz"), 0.001, 50000.0),  # None = leave CAT alone
            "enabled":  bool(e.get("enabled", True)),
        })
    cfg["entries"] = entries
    return cfg


# ── Plan ─────────────────────────────────────────────────────

def _all_sats() -> dict:
    """norad_id -> [(module name, satellite dict), ...] over every module file."""
    out = {}
    for name in mod_mgr.list_modules():
        try:
            mod = mod_mgr.load_module(name)
        except Exception:
            continue
        for s in mod.get("satellites", []):
            if s.get("norad_id") and s.get("tle_line1") and s.get("tle_line2"):
                out.setdefault(str(s["norad_id"]), []).append((name, s))
    return out


def _age_text(days: float) -> str:
    return "%.1f years" % (days / 365.25) if days >= 730 else "%d days" % round(days)


def _mark_conflicts(passes: list, lead_sec: int, tail_sec: int) -> list:
    """Passes that overlap (including lead / tail) cannot both be tracked: the one with
    the higher max elevation wins, the other is marked skipped."""
    lead, tail = lead_sec / 86400.0, tail_sec / 86400.0
    kept = []                                    # (start, end, pass) of the winners
    for p in sorted(passes, key=lambda x: -x["max_el"]):
        a, b = p["aos_jd"] - lead, p["los_jd"] + tail
        clash = next((k for k in kept if a < k[1] and b > k[0]), None)
        if clash:
            p["skipped"] = True
            p["reason"] = "overlaps %s (%.0f°)" % (clash[2]["name"], clash[2]["max_el"])
        else:
            p["skipped"] = False
            p["reason"] = ""
            kept.append((a, b, p))
    return sorted(passes, key=lambda x: x["aos_jd"])


def compute_plan(cfg: dict, obs: tuple, start_jd: float,
                 horizon_h: float = HORIZON_H) -> tuple:
    """(passes, problems) for the enabled entries, from ``start_jd`` for ``horizon_h`` hours.
    ``obs`` = (lat, lon, alt_m, station_min_el)."""
    lat, lon, alt_m, station_min_el = obs
    sats    = _all_sats()
    end_jd  = start_jd + horizon_h / 24.0
    passes, problems = [], []

    for e in cfg["entries"]:
        if not e["enabled"]:
            continue
        cands = sats.get(e["norad_id"]) or []
        if not cands:
            problems.append({"norad_id": e["norad_id"], "name": e["name"],
                             "reason": "not in any module"})
            continue
        module, sat = next((c for c in cands if c[0] == e["module"]), cands[0])
        st = satcat.cached_status(e["norad_id"])
        if st and st.get("decayed"):
            problems.append({"norad_id": e["norad_id"], "name": sat["name"], "reason": "decayed"})
            continue
        try:
            rec = Satrec.twoline2rv(sat["tle_line1"], sat["tle_line2"])
        except Exception:
            problems.append({"norad_id": e["norad_id"], "name": sat["name"], "reason": "bad element set"})
            continue
        # SGP4 is only meaningful near the element set's epoch. Years-old elements (typical for
        # objects that have decayed) propagate to nonsense - impossible orbits whose "passes"
        # would also crowd out the real satellites - so they are not planned at all.
        age_d, limit = start_jd - (rec.jdsatepoch + rec.jdsatepochF), cfg["max_tle_age_days"]
        if limit and age_d > limit:
            problems.append({"norad_id": e["norad_id"], "name": sat["name"],
                             "reason": "element set is %s old (limit %d days) - update TLEs or raise the limit"
                                       % (_age_text(age_d), limit)})
            continue

        thr     = e["min_el"] if e["min_el"] is not None else cfg["default_min_el"]
        base_el = max(0.0, min(station_min_el, thr))     # AOS / LOS are found at this elevation
        cursor, guard, found = start_jd, 0, 0
        while cursor < end_jd and guard < 60:
            guard += 1
            p = find_next_pass(rec, lat, lon, alt_m, base_el,
                               start_jd=cursor, horizon_days=1)
            if not p or p["aos_jd"] > end_jd:
                break
            cursor = p["los_jd"] + 1.0 / 1440.0
            if p["max_el"] < thr:
                continue
            found += 1
            passes.append({
                "norad_id":  e["norad_id"],
                "name":      sat["name"],
                "module":    module,
                "aos_jd":    p["aos_jd"],   "los_jd":    p["los_jd"],
                "aos_dt":    p["aos_dt"],   "los_dt":    p["los_dt"],
                "max_el":    p["max_el"],   "duration":  p["duration"],
                "aos_az":    p["aos_az"],   "los_az":    p["los_az"],
                "threshold": thr,
                "down_mhz":  e["down_mhz"],
            })
        if not found:
            problems.append({"norad_id": e["norad_id"], "name": sat["name"],
                             "reason": "no pass reaching %.0f° in %d h" % (thr, horizon_h),
                             "info": True})
    return _mark_conflicts(passes, cfg["lead_sec"], cfg["tail_sec"]), problems


# ── State machine ────────────────────────────────────────────

class AutoTracker:
    def __init__(self):
        self.cfg      = normalize({})
        self.plan     = []
        self.problems = []
        self.current  = None            # the pass being tracked right now
        self._done    = {}              # norad_id -> [(aos_jd, los_jd)] already handled
        self._dirty   = True
        self._plan_ts = 0.0
        self._busy    = False
        self._get_obs = lambda: (59.33, 18.07, 20.0, 5.0)

    def bind(self, get_obs):
        """``get_obs()`` -> (lat, lon, alt_m, station_min_el)."""
        self._get_obs = get_obs

    def set_config(self, raw: dict):
        self.cfg    = normalize(raw)
        self._dirty = True
        if not self.cfg["entries"]:             # nothing to plan: drop the old plan too
            self.plan, self.problems = [], []

    def invalidate(self):
        self._dirty = True

    # -- plan, rebuilt in a thread so the 1 Hz loop never waits for it ----
    def _pending(self) -> bool:
        return self._busy or (self._dirty and bool(self.cfg["entries"]))

    def maybe_recompute(self):
        """The plan is kept up to date whenever the list has satellites, also while auto-track
        is switched off, so the dialog can preview what would be tracked."""
        if not self.cfg["entries"] or self._busy:
            return
        if not self._dirty and time.time() - self._plan_ts < REPLAN_SEC:
            return
        self._busy, self._dirty = True, False
        threading.Thread(target=self._recompute, daemon=True).start()

    def _recompute(self):
        try:
            start = now_jd() - LOOKBACK_MIN / 1440.0
            passes, problems = compute_plan(self.cfg, self._get_obs(), start)
            self.plan, self.problems = passes, problems
            self._plan_ts = time.time()
        except Exception:
            traceback.print_exc()
            self._plan_ts = time.time()
        finally:
            self._busy = False

    # -- 1 Hz decision -----------------------------------------------------
    def _was_done(self, p: dict) -> bool:
        return any(abs(p["aos_jd"] - a) < SAME_PASS_DAYS
                   for a, _ in self._done.get(p["norad_id"], []))

    def tick(self, jd: float) -> list:
        """[("start" | "end", pass)] for this second."""
        events = []
        lead = self.cfg["lead_sec"] / 86400.0
        tail = self.cfg["tail_sec"] / 86400.0

        cur = self.current
        if cur:
            wanted = any(e["enabled"] and e["norad_id"] == cur["norad_id"]
                         for e in self.cfg["entries"])
            if not self.cfg["enabled"] or not wanted or jd >= cur["los_jd"] + tail:
                events.append(("end", cur))
                self._done.setdefault(cur["norad_id"], []).append((cur["aos_jd"], cur["los_jd"]))
                self.current = None

        if self.cfg["enabled"] and not self.current:
            ids  = {e["norad_id"] for e in self.cfg["entries"] if e["enabled"]}
            live = [p for p in self.plan
                    if p["norad_id"] in ids and not p["skipped"] and not self._was_done(p)
                    and p["aos_jd"] - lead <= jd < p["los_jd"] + tail]
            if live:
                best = max(live, key=lambda p: p["max_el"])
                self.current = dict(best)
                events.append(("start", self.current))

        for nid in list(self._done):             # forget old history
            self._done[nid] = [(a, l) for a, l in self._done[nid] if l > jd - 1.0]
            if not self._done[nid]:
                del self._done[nid]
        return events

    # -- read-outs ----------------------------------------------------------
    def _upcoming(self, jd: float) -> list:
        return [p for p in self.plan if p["los_jd"] + self.cfg["tail_sec"] / 86400.0 > jd]

    def snapshot(self) -> dict:
        jd = now_jd()
        return {"config": self.cfg, "plan": self._upcoming(jd), "current": self.current,
                "problems": self.problems, "computing": self._pending(),
                "now_jd": jd}

    def chip(self) -> dict:
        jd  = now_jd()
        nxt = next((p for p in self._upcoming(jd)
                    if not p["skipped"] and not self._was_done(p)
                    and (not self.current or p["aos_jd"] > self.current["los_jd"])), None)
        brief = lambda p: p and {k: p[k] for k in ("name", "aos_jd", "los_jd", "max_el")}
        return {"enabled": self.cfg["enabled"], "current": brief(self.current),
                "next": brief(nxt), "default_min_el": self.cfg["default_min_el"],
                "computing": self._pending()}


auto = AutoTracker()


# ── Commands ─────────────────────────────────────────────────

def _split(template: str) -> list:
    parts = shlex.split(template, posix=(os.name != "nt"))
    if os.name == "nt":                       # non-POSIX split keeps the quotes
        parts = [x[1:-1] if len(x) > 1 and x[0] == x[-1] and x[0] in "\"'" else x for x in parts]
    return parts


def _stamp(jd: float) -> str:
    return jd_to_dt(jd).strftime("%Y%m%dT%H%M%SZ")


def build_command(template: str, event: str, p: dict) -> tuple:
    """(argv, env) for a user command. NO shell is involved: the command line is split
    into arguments and only these placeholders are substituted (values are file-name
    safe): {event} {sat} {norad} {module} {max_el} {aos} {los} {duration} {down_mhz}."""
    safe = _SAFE.sub("_", p.get("name", "")).strip("_") or "sat"
    vals = {
        "event":    event,
        "sat":      safe,
        "norad":    _SAFE.sub("_", str(p.get("norad_id", ""))),
        "module":   _SAFE.sub("_", p.get("module", "")),
        "max_el":   "%.0f" % p.get("max_el", 0),
        "aos":      _stamp(p["aos_jd"]),
        "los":      _stamp(p["los_jd"]),
        "duration": str(int(p.get("duration", 0))),
        "down_mhz": "" if not p.get("down_mhz") else "%.6f" % p["down_mhz"],
    }
    argv = _split(template)
    for k, v in vals.items():
        argv = [a.replace("{%s}" % k, v) for a in argv]
    env = dict(os.environ)
    env.update({
        "K73_EVENT": event, "K73_SAT": p.get("name", ""), "K73_SAT_SAFE": safe,
        "K73_NORAD": vals["norad"], "K73_MODULE": p.get("module", ""),
        "K73_MAX_EL": vals["max_el"], "K73_AOS_UTC": vals["aos"], "K73_LOS_UTC": vals["los"],
        "K73_DURATION": vals["duration"], "K73_DOWN_MHZ": vals["down_mhz"],
        "K73_AOS_AZ": str(p.get("aos_az", "")), "K73_LOS_AZ": str(p.get("los_az", "")),
    })
    return argv, env


def run_command(template: str, event: str, p: dict) -> str:
    """Start the command (not waited for). Returns "" or an error message."""
    if not template or not template.strip():
        return ""
    try:
        argv, env = build_command(template, event, p)
        if not argv:
            return ""
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        proc = subprocess.Popen(argv, env=env, cwd=DATA_DIR, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=flags)
        threading.Thread(target=proc.wait, daemon=True).start()      # reap, never block
        return ""
    except Exception as e:
        return "%s command failed: %s" % (event, e)
