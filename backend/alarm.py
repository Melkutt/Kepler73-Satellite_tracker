# -*- coding: utf-8 -*-
"""
Kepler73 - alarm.py
Pass alarm system with selectable sounds via pygame.
Sounds: CW 'AOS', three beeps, rising tone, long beep.
"""

import threading
import time
import numpy as np
import pygame
from backend.config import ALARM_DEFAULT_MIN

# ── Morse code ────────────────────────────────────────────────
MORSE = { 'A': '.-', 'O': '---', 'S': '...' }

# CW parameters (PARIS standard)
WPM         = 18
FREQ_HZ     = 700
SAMPLE_RATE = 44100
DIT_SEC     = 1.2 / WPM
DAH_SEC     = 3 * DIT_SEC
SYM_GAP     = DIT_SEC
LET_GAP     = 3 * DIT_SEC

# ── Sound options ─────────────────────────────────────────────
SOUND_OPTIONS = {
    'cw_aos':     'CW - AOS in morse code',
    'three_beeps':'Three short beeps',
    'rising':     'Rising tone (radar)',
    'long_beep':  'One long beep',
}

# ── State ─────────────────────────────────────────────────────
_muted      = False
_alarm_min  = ALARM_DEFAULT_MIN
_sound      = 'cw_aos'
_fired      = {}

# ── pygame init ───────────────────────────────────────────────
_pygame_ready = False

def _init_pygame():
    global _pygame_ready
    if not _pygame_ready:
        try:
            pygame.mixer.init(frequency=SAMPLE_RATE, size=-16,
                              channels=1, buffer=512)
            _pygame_ready = True
        except Exception as e:
            print(f'[Kepler73] pygame init failed: {e}')

# ── Tone generation ───────────────────────────────────────────

def _make_tone(duration_sec, freq=FREQ_HZ, fade_ms=8):
    """Generate a sine wave tone as a pygame Sound object."""
    n    = int(SAMPLE_RATE * duration_sec)
    t    = np.linspace(0, duration_sec, n, endpoint=False)
    wave = (np.sin(2 * np.pi * freq * t) * 32767).astype(np.int16)

    # Short fade in/out to avoid clicks
    fade_n = min(int(SAMPLE_RATE * fade_ms / 1000), n // 2)
    ramp   = np.linspace(0, 1, fade_n)
    wave[:fade_n]  = (wave[:fade_n]  * ramp).astype(np.int16)
    wave[-fade_n:] = (wave[-fade_n:] * ramp[::-1]).astype(np.int16)

    channels = pygame.mixer.get_init()[2]
    if channels == 2:
        stereo = np.column_stack((wave, wave))
        return pygame.sndarray.make_sound(stereo)
    return pygame.sndarray.make_sound(wave)

def _play_tone(duration_sec, freq=FREQ_HZ):
    """Play a tone and wait for it to finish."""
    _make_tone(duration_sec, freq).play()
    time.sleep(duration_sec)

# ── Sound implementations ─────────────────────────────────────

def _play_cw_aos():
    """Play CW 'AOS': .- --- ..."""
    for i, letter in enumerate('AOS'):
        pattern = MORSE[letter]
        for j, sym in enumerate(pattern):
            dur = DIT_SEC if sym == '.' else DAH_SEC
            _play_tone(dur)
            if j < len(pattern) - 1:
                time.sleep(SYM_GAP)
        if i < 2:
            time.sleep(LET_GAP)

def _play_three_beeps():
    """Three short beeps at 880 Hz."""
    for i in range(3):
        _play_tone(0.12, 880)
        if i < 2:
            time.sleep(0.1)

def _play_rising():
    """Rising tone from 400 Hz to 1200 Hz over 0.8 seconds."""
    steps = 12
    for i in range(steps):
        freq = int(400 + (800 * i / (steps - 1)))
        _play_tone(0.07, freq)

def _play_long_beep():
    """One long beep at 700 Hz for 1.2 seconds."""
    _play_tone(1.2, 700)

# ── Sound dispatch ────────────────────────────────────────────

def _play_sound():
    """Play the currently selected alarm sound."""
    _init_pygame()
    if not _pygame_ready:
        return
    try:
        if   _sound == 'cw_aos':      _play_cw_aos()
        elif _sound == 'three_beeps': _play_three_beeps()
        elif _sound == 'rising':      _play_rising()
        elif _sound == 'long_beep':   _play_long_beep()
    except Exception as e:
        print(f'[Kepler73] Alarm sound error: {e}')

# ── Public API ────────────────────────────────────────────────

def set_muted(muted: bool):
    global _muted
    _muted = muted

def is_muted() -> bool:
    return _muted

def set_alarm_minutes(minutes: int):
    global _alarm_min
    _alarm_min = max(1, int(minutes))

def get_alarm_minutes() -> int:
    return _alarm_min

def set_sound(sound: str):
    global _sound
    if sound in SOUND_OPTIONS:
        _sound = sound

def get_sound() -> str:
    return _sound

def get_sound_options() -> dict:
    return SOUND_OPTIONS

def reset_all_fired():
    """Clear all fired flags, e.g. when module changes."""
    _fired.clear()

def reset_fired(norad_id: str):
    """Clear fired flags for one satellite."""
    _fired.pop(norad_id, None)

def trigger_alarm(norad_id: str, alarm_type: str, sat_name: str, aos_jd: float):
    """
    Trigger alarm for a satellite pass event.
    Returns alarm dict to emit to frontend, or None if already fired.
    Uses aos_jd as pass identifier so a new pass always re-triggers.
    """
    key = f"{norad_id}:{aos_jd:.6f}:{alarm_type}"
    if key in _fired:
        return None
    _fired[key] = True

    if not _muted:
        threading.Thread(target=_play_sound, daemon=True).start()

    return {
        'norad_id':  norad_id,
        'sat_name':  sat_name,
        'type':      alarm_type,
        'alarm_min': _alarm_min,
    }

def check_alarms(sat_records: dict, satellites: list,
                 config: dict, now_jd_fn, find_pass_fn) -> list:
    """
    Check all satellites for upcoming passes.
    Returns list of alarm events to emit to frontend.
    """
    from backend.sgp4_engine import now_jd
    events = []
    jd     = now_jd()
    lat    = config.get('lat',    59.33)
    lon    = config.get('lon',    18.07)
    alt    = config.get('alt',    20.0)
    min_el = config.get('min_el', 5.0)
    alarm_min = config.get('alarm_min', _alarm_min)

    for sat in satellites:
        norad = sat['norad_id']
        rec   = sat_records.get(norad)
        if not rec:
            continue
        p = find_pass_fn(rec, lat, lon, alt, min_el)
        if not p:
            continue

        aos_jd    = p['aos_jd']
        dt_to_aos = (aos_jd - jd) * 86400   # seconds until AOS
        warn_sec  = alarm_min * 60

        # Warning alarm: within X minutes of AOS
        if 0 < dt_to_aos <= (warn_sec + 5):
            ev = trigger_alarm(norad, 'warning', sat['name'], aos_jd)
            if ev:
                ev['seconds_to_aos'] = int(dt_to_aos)
                ev['aos_dt']         = p['aos_dt']
                ev['max_el']         = p['max_el']
                events.append(ev)

    # Prune old fired keys to avoid memory growth
    if len(_fired) > 500:
        _fired.clear()

    return events