# -*- coding: utf-8 -*-
"""
Kepler73 – SGP4 Engine
Calculates satellite positions, ground tracks and passes.
"""

import math
from datetime import datetime, timezone
from sgp4.api import Satrec, jday

DEG2RAD = math.pi / 180.0
RAD2DEG = 180.0 / math.pi
TWOPI   = 2.0 * math.pi
XKMPER  = 6378.137
F_FLAT  = 1.0 / 298.257223563
MFACTOR = 7.292115e-5


# ── Time ──────────────────────────────────────────────────────

def now_jd():
    dt = datetime.now(timezone.utc)
    jd_day, jd_frac = jday(dt.year, dt.month, dt.day,
                           dt.hour, dt.minute,
                           dt.second + dt.microsecond / 1e6)
    return jd_day + jd_frac


def _split_jd(jd: float):
    jd_day  = int(jd)
    jd_frac = jd - jd_day
    return float(jd_day), float(jd_frac)


def jd_to_dt(jd):
    jd = jd + 0.5
    Z  = int(jd); F = jd - Z
    A  = Z if Z < 2299161 else (lambda a: Z+1+a-a//4)(
         int((Z-1867216.25)/36524.25))
    B  = A+1524; C=int((B-122.1)/365.25)
    D  = int(365.25*C); E=int((B-D)/30.6001)
    day   = B-D-int(30.6001*E)
    month = E-1 if E < 14 else E-13
    year  = C-4716 if month > 2 else C-4715
    f2=F*24; h=int(f2); f2=(f2-h)*60; m=int(f2)
    f2=(f2-m)*60; s=int(f2); us=int((f2-s)*1e6)
    return datetime(year,month,day,h,m,s,us,tzinfo=timezone.utc)


# ── Coordinates ───────────────────────────────────────────────

def _gmst(jd):
    """Greenwich Mean Sidereal Time from Julian Date."""
    jd0 = int(jd - 0.5) + 0.5
    T   = (jd0 - 2451545.0) / 36525.0
    g   = (6.697374558 + 2400.0513369 * T) * (math.pi / 12.0)
    return (g + MFACTOR * (jd - jd0) * 86400.0) % TWOPI


def eci_to_geo(x, y, z, jd):
    """Convert ECI coordinates (km) to (lat°, lon°, alt km)."""
    gmst = _gmst(jd)
    cg = math.cos(gmst); sg = math.sin(gmst)
    xe =  x*cg + y*sg
    ye = -x*sg + y*cg
    lon = math.atan2(ye, xe) * RAD2DEG
    p   = math.sqrt(xe**2 + ye**2)
    lat = math.atan2(z, p*(1-F_FLAT))
    for _ in range(6):
        N   = XKMPER / math.sqrt(1-(2*F_FLAT-F_FLAT**2)*math.sin(lat)**2)
        lat = math.atan2(z+(2*F_FLAT-F_FLAT**2)*N*math.sin(lat), p)
    alt = (p/math.cos(lat)
           - XKMPER/math.sqrt(1-(2*F_FLAT-F_FLAT**2)*math.sin(lat)**2))
    return lat*RAD2DEG, lon, alt


def eci_to_azel(obs_lat, obs_lon, obs_alt_km, sx, sy, sz, jd):
    """
    Convert ECI satellite position to Az°, El°, Range km as seen from observer.
    Az measured clockwise from North: N=0, E=90, S=180, W=270.
    Based on Vallado "Fundamentals of Astrodynamics" topocentric algorithm.
    """
    gmst  = _gmst(jd)
    lat_r = obs_lat * DEG2RAD
    lon_r = obs_lon * DEG2RAD
    e2    = 2*F_FLAT - F_FLAT**2

    # Observer ECI position
    N  = XKMPER / math.sqrt(1 - e2 * math.sin(lat_r)**2)
    h  = obs_alt_km
    # ECEF
    ox_ecef = (N + h) * math.cos(lat_r) * math.cos(lon_r)
    oy_ecef = (N + h) * math.cos(lat_r) * math.sin(lon_r)
    oz_ecef = (N * (1 - e2) + h)        * math.sin(lat_r)
    # Rotate ECEF to ECI
    cg = math.cos(gmst); sg = math.sin(gmst)
    ox = ox_ecef*cg - oy_ecef*sg
    oy = ox_ecef*sg + oy_ecef*cg
    oz = oz_ecef

    # Range vector in ECI
    rx = sx - ox
    ry = sy - oy
    rz = sz - oz
    rng = math.sqrt(rx*rx + ry*ry + rz*rz)

    # Rotate range vector to SEZ using observer lat and LST
    lst = (gmst + lon_r) % TWOPI
    sl  = math.sin(lat_r); cl = math.cos(lat_r)
    sls = math.sin(lst);   cls = math.cos(lst)

    rS =  sl*cls*rx + sl*sls*ry - cl*rz
    rE = -sls*rx    + cls*ry
    rZ =  cl*cls*rx + cl*sls*ry + sl*rz

    el = math.asin(rZ / rng)
    az = math.atan2(rE, -rS) % TWOPI

    return az * RAD2DEG, el * RAD2DEG, rng


def footprint_km(alt_km):
    angle = math.acos(XKMPER / (XKMPER + alt_km))
    return XKMPER * angle


# ── Propagation ───────────────────────────────────────────────

def propagate(sat_rec: Satrec, jd: float):
    jd_day, jd_frac = _split_jd(jd)
    e, r, v = sat_rec.sgp4(jd_day, jd_frac)
    if e != 0:
        return None
    lat, lon, alt = eci_to_geo(r[0], r[1], r[2], jd)
    return lat, lon, alt, r[0], r[1], r[2], v[0], v[1], v[2]


def radial_velocity_ms(obs_lat, obs_lon, obs_alt_m,
                       sx, sy, sz, vx, vy, vz, jd):
    """
    Radial velocity in m/s. Positive = receding (downshift), negative = approaching (upshift).
    """
    lat_r  = obs_lat * DEG2RAD
    lon_r  = obs_lon * DEG2RAD
    gmst   = _gmst(jd)
    alt_km = obs_alt_m / 1000.0

    N  = XKMPER / math.sqrt(1 - (2*F_FLAT - F_FLAT**2) * math.sin(lat_r)**2)
    ox = (N + alt_km) * math.cos(lat_r) * math.cos(lon_r + gmst)
    oy = (N + alt_km) * math.cos(lat_r) * math.sin(lon_r + gmst)
    oz = (N * (1 - (2*F_FLAT - F_FLAT**2)) + alt_km) * math.sin(lat_r)

    rx = sx - ox; ry = sy - oy; rz = sz - oz
    rng = math.sqrt(rx*rx + ry*ry + rz*rz)
    if rng == 0:
        return 0.0

    we  = 7.2921150e-5
    ovx = -we * oy
    ovy =  we * ox
    ovz = 0.0

    dvx = vx - ovx; dvy = vy - ovy; dvz = vz - ovz
    v_radial = (rx*dvx + ry*dvy + rz*dvz) / rng
    return v_radial * 1000.0


def forward_track(sat_rec: Satrec, jd_start: float,
                  minutes: int = 90, step_sec: int = 20):
    points  = []
    step_jd = step_sec / 86400.0
    n       = int(minutes * 60 / step_sec)
    jd      = jd_start
    for _ in range(n):
        res = propagate(sat_rec, jd)
        if res:
            points.append([res[0], res[1]])
        jd += step_jd
    return points


# ── Pass prediction ───────────────────────────────────────────

def _el_at(sat_rec, lat, lon, alt_km, jd):
    res = propagate(sat_rec, jd)
    if not res:
        return -90.0
    _, el, _ = eci_to_azel(lat, lon, alt_km, res[3], res[4], res[5], jd)
    return el


def _bisect(sat_rec, lat, lon, alt_km, jd_lo, jd_hi, thr, rising, n=25):
    for _ in range(n):
        m  = (jd_lo + jd_hi) / 2
        el = _el_at(sat_rec, lat, lon, alt_km, m)
        if (el >= thr) == rising:
            jd_hi = m
        else:
            jd_lo = m
    return (jd_lo + jd_hi) / 2


def find_next_pass(sat_rec: Satrec, lat: float, lon: float,
                   alt_m: float, min_el: float = 5.0,
                   start_jd: float = None,
                   horizon_days: int = 7) -> dict | None:
    alt_km = alt_m / 1000.0
    if start_jd is None:
        start_jd = now_jd()

    step   = 20.0 / 86400.0
    end_jd = start_jd + horizon_days
    jd     = start_jd

    in_pass   = False
    aos_jd    = None
    max_el_v  = -90.0
    max_el_jd = None
    max_az    = 0.0
    aos_az    = 0.0

    # Skip ongoing pass
    if _el_at(sat_rec, lat, lon, alt_km, start_jd) >= min_el:
        while jd < end_jd:
            if _el_at(sat_rec, lat, lon, alt_km, jd) < min_el:
                break
            jd += step

    while jd < end_jd:
        el = _el_at(sat_rec, lat, lon, alt_km, jd)

        if not in_pass and el >= min_el:
            jd_aos = _bisect(sat_rec, lat, lon, alt_km, jd-step, jd, min_el, True)
            in_pass = True; aos_jd = jd_aos
            res = propagate(sat_rec, jd_aos)
            if res:
                aos_az, _, _ = eci_to_azel(lat, lon, alt_km, res[3], res[4], res[5], jd_aos)
            max_el_v = el; max_el_jd = jd

        elif in_pass:
            if el > max_el_v:
                max_el_v = el; max_el_jd = jd
                res = propagate(sat_rec, jd)
                if res:
                    max_az, _, _ = eci_to_azel(lat, lon, alt_km, res[3], res[4], res[5], jd)
            if el < min_el:
                jd_los = _bisect(sat_rec, lat, lon, alt_km, jd-step, jd, min_el, False)
                res = propagate(sat_rec, jd_los)
                los_az = 0.0
                if res:
                    los_az, _, _ = eci_to_azel(lat, lon, alt_km, res[3], res[4], res[5], jd_los)
                dur = (jd_los - aos_jd) * 86400.0

                max_v = 0.0
                step_pass = (jd_los - aos_jd) / 20.0
                jd_s = aos_jd
                while jd_s <= jd_los:
                    res_s = propagate(sat_rec, jd_s)
                    if res_s:
                        v_s = abs(radial_velocity_ms(
                            lat, lon, alt_km * 1000,
                            res_s[3], res_s[4], res_s[5],
                            res_s[6], res_s[7], res_s[8], jd_s))
                        if v_s > max_v:
                            max_v = v_s
                    jd_s += step_pass

                return {
                    "aos_jd":       aos_jd,
                    "aos_dt":       jd_to_dt(aos_jd).strftime("%Y-%m-%d %H:%M:%S"),
                    "max_el_jd":    max_el_jd,
                    "max_el_dt":    jd_to_dt(max_el_jd).strftime("%Y-%m-%d %H:%M:%S"),
                    "los_jd":       jd_los,
                    "los_dt":       jd_to_dt(jd_los).strftime("%Y-%m-%d %H:%M:%S"),
                    "max_el":       round(max_el_v, 1),
                    "aos_az":       round(aos_az, 1),
                    "max_az":       round(max_az, 1),
                    "los_az":       round(los_az, 1),
                    "duration":     round(dur),
                    "max_v_radial": round(max_v, 1),
                }
        jd += step
    return None


def get_pass_track(sat_rec: Satrec, lat: float, lon: float,
                   alt_m: float, pass_info: dict,
                   steps: int = 120) -> list:
    alt_km = alt_m / 1000.0
    t0 = pass_info["aos_jd"]
    t1 = pass_info["los_jd"]
    dt = (t1 - t0) / steps
    points = []
    for i in range(steps + 1):
        jd = t0 + i * dt
        res = propagate(sat_rec, jd)
        if res:
            az, el, _ = eci_to_azel(lat, lon, alt_km, res[3], res[4], res[5], jd)
            if el >= 0:
                points.append([round(az, 1), round(el, 1)])
    return points


def compass(az: float) -> str:
    d = ["N","NNE","NE","ENE","E","ESE","SE","SSE",
         "S","SSW","SW","WSW","W","WNW","NW","NNW"]
    return d[int((az + 11.25) / 22.5) % 16]