# -*- coding: utf-8 -*-
"""
Kepler73 - Configuration & constants
"""

import os
import sys

# ── Paths ─────────────────────────────────────────────────────
# Support both normal Python and PyInstaller frozen executable
if getattr(sys, 'frozen', False):
    # Running as PyInstaller bundle
    BASE_DIR = sys._MEIPASS
    # Writable data dir in user home
    _USER_DATA = os.path.join(os.path.expanduser('~'), '.kepler73')
    DATA_DIR = _USER_DATA
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    DATA_DIR = os.path.join(BASE_DIR, "data")
MODULES_DIR = os.path.join(DATA_DIR, "modules")
TLE_DIR     = os.path.join(DATA_DIR, "tle_cache")
TILE_DIR    = os.path.join(DATA_DIR, "tile_cache")
APP_CONFIG  = os.path.join(DATA_DIR, "app_config.json")

for d in [MODULES_DIR, TLE_DIR, TILE_DIR]:
    os.makedirs(d, exist_ok=True)

# ── App info ──────────────────────────────────────────────────
APP_NAME    = "Kepler73"
APP_VERSION = "0.3.0"
AUTHOR      = "SA1CKW"

# ── Celestrak ─────────────────────────────────────────────────
CELESTRAK_URL   = "https://celestrak.org/NORAD/elements/gp.php"
NETWORK_TIMEOUT = 10

# ── TLE source ────────────────────────────────────────────────
# "celestrak"           – fetch online from Celestrak (default)
# "file"                – offline only, from TLE_FILE
# "file_then_celestrak" – try TLE_FILE first, fall back to Celestrak
TLE_SOURCE = "celestrak"
TLE_FILE   = ""            # path to a .txt/.tle/.json/.csv file or a folder of them

# ── SGP4 ──────────────────────────────────────────────────────
TLE_MAX_AGE_DAYS  = 7
PASS_HORIZON_DAYS = 7
PASS_MIN_EL       = 5.0
TRACK_STEP_SEC    = 20
TRACK_AHEAD_MIN   = 90
LIVE_UPDATE_SEC   = 1

# TLE auto-update: the background thread wakes every TLE_UPDATE_INTERVAL and
# only re-fetches element sets already older than TLE_STALE_SEC. Celestrak
# publishes new elements roughly every 2 h and asks clients not to poll harder
# than the data changes; for amateur pass prediction a ~once-a-day refresh is
# plenty (a 24 h-old LEO TLE is within ~1 s on pass times). Net effect: at most
# ~1 Celestrak request per satellite per day, and none on a fresh module.
TLE_UPDATE_INTERVAL = 6 * 3600    # seconds between auto-update sweeps
TLE_STALE_SEC       = 18 * 3600   # only re-fetch an element set older than this
CELESTRAK_BACKOFF_SEC = 8 * 3600  # stay away this long after a rate-limit block
TLE_MANUAL_COOLDOWN_SEC = 2 * 3600  # min gap between manual "Update all TLEs" (Celestrak sources)

# ── Map defaults ──────────────────────────────────────────────
MAP_CENTER_LAT = 59.3293
MAP_CENTER_LON = 18.0686
MAP_ZOOM       = 3

# ── Pass alarm ────────────────────────────────────────────────
ALARM_DEFAULT_MIN = 3       # Default minutes before AOS to trigger alarm
ALARM_SOUND_MUTED = False   # Default mute state

# ── Satellite colors ──────────────────────────────────────────
SAT_COLORS = [
    "#00e676", "#00acc1", "#ffd54f", "#ef5350",
    "#ab47bc", "#26c6da", "#ffca28", "#66bb6a",
    "#ff7043", "#42a5f5", "#ec407a", "#8d6e63",
]