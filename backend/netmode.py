# -*- coding: utf-8 -*-
"""
Kepler73 - "offline station" switch.

When on, nothing in Kepler73 contacts the public internet services (Celestrak,
SatNOGS, OpenStreetMap / Esri tiles, Nominatim, Open-Meteo): every caller checks
``is_offline()`` first and uses what is on disk instead. The user's own TLE
sources (files, network shares or a LAN web server) are NOT affected - they are
how an offline station still gets fresh element sets.
"""

_offline = False


def set_offline(flag) -> None:
    global _offline
    _offline = bool(flag)


def is_offline() -> bool:
    return _offline
