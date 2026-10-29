# -*- coding: utf-8 -*-
"""
Kepler73 - rotator pointing logic.

Phase 1: an elevation gate (park below it, track above it) and a deadband so a
slow mechanical rotator is not hammered with sub-degree updates. Pure functions,
no socket I/O.

Phase 2 will add AOS pre-positioning; phase 3 north-crossing / flip planning.
"""


def _norm_az(az: float) -> float:
    return az % 360.0


def desired_pointing(sat_az: float, sat_el: float, cfg: dict):
    """Where the rotor should point right now: (az, el, mode).

    mode is "tracking" when the satellite is above cfg["min_el"], else "parked"
    (pointing at cfg["park_az"] / cfg["park_el"]).
    """
    min_el = float(cfg.get("min_el", 3.0))
    if sat_el < min_el:
        return (_norm_az(float(cfg.get("park_az", 0.0))),
                max(0.0, float(cfg.get("park_el", 0.0))),
                "parked")
    return (_norm_az(sat_az), max(0.0, min(90.0, sat_el)), "tracking")


def beyond_deadband(target, last, az_db: float, el_db: float) -> bool:
    """True if ``target`` (az, el) differs from the last commanded position by
    more than the deadband on either axis (or there is no last position)."""
    if last is None:
        return True
    d_az = abs(((target[0] - last[0] + 180.0) % 360.0) - 180.0)
    d_el = abs(target[1] - last[1])
    return d_az >= az_db or d_el >= el_db
