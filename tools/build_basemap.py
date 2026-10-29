#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Rebuild the built-in offline base map: frontend/vendor/naturalearth/{land,lakes,borders}.json

Source: Natural Earth 1:50m vector data (public domain, https://www.naturalearthdata.com),
fetched as GeoJSON from the nvkelso/natural-earth-vector mirror. The files are stripped to
bare geometry and coordinates are rounded to 2 decimals (~1 km) with repeated points
removed, which brings the three layers to roughly 2 MB in total.

The RESULT is committed to the repository - nobody needs to run this to use Kepler73.
It only documents where the data came from and lets you regenerate or change it:

    python tools/build_basemap.py            (needs internet)
"""

import os
import sys
import json
import urllib.request

BASE = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/"
OUT  = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "frontend", "vendor", "naturalearth")
DECIMALS = 2

LAYERS = {
    "land":    "ne_50m_land.geojson",
    "lakes":   "ne_50m_lakes.geojson",
    "borders": "ne_50m_admin_0_boundary_lines_land.geojson",
}


def _simplify(points):
    """Round and drop consecutive duplicates."""
    out = []
    for lon, lat, *_ in points:
        p = [round(lon, DECIMALS), round(lat, DECIMALS)]
        if not out or p != out[-1]:
            out.append(p)
    return out


def _line(points):
    pts = _simplify(points)
    return pts if len(pts) >= 2 else None


def _ring(points):
    pts = _simplify(points)
    if pts and pts[0] != pts[-1]:
        pts.append(pts[0])
    return pts if len(pts) >= 4 else None


def _geometry(geom):
    t, c = geom["type"], geom["coordinates"]
    if t == "LineString":
        l = _line(c)
        return {"type": t, "coordinates": l} if l else None
    if t == "MultiLineString":
        ls = [l for l in (_line(x) for x in c) if l]
        return {"type": t, "coordinates": ls} if ls else None
    if t == "Polygon":
        rings = [r for r in (_ring(x) for x in c) if r]
        return {"type": t, "coordinates": rings} if rings and _ring(c[0]) else None
    if t == "MultiPolygon":
        polys = []
        for poly in c:
            rings = [r for r in (_ring(x) for x in poly) if r]
            if rings and _ring(poly[0]):
                polys.append(rings)
        return {"type": t, "coordinates": polys} if polys else None
    return None


def main():
    os.makedirs(OUT, exist_ok=True)
    for name, fname in LAYERS.items():
        url = BASE + fname
        print(f"downloading {url}")
        req = urllib.request.Request(url, headers={"User-Agent": "Kepler73-basemap-builder"})
        with urllib.request.urlopen(req, timeout=120) as r:
            src = json.loads(r.read().decode("utf-8"))
        feats = []
        for f in src["features"]:
            g = _geometry(f["geometry"]) if f.get("geometry") else None
            if g:
                feats.append({"type": "Feature", "properties": {}, "geometry": g})
        path = os.path.join(OUT, name + ".json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"type": "FeatureCollection", "features": feats}, fh, separators=(",", ":"))
        print(f"  -> {path}: {len(feats)} features, {os.path.getsize(path) / 1024:.0f} KB")


if __name__ == "__main__":
    sys.exit(main())
