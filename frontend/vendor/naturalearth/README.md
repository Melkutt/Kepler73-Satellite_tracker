# Built-in offline base map

`land.json`, `lakes.json` and `borders.json` are the world map Kepler73 draws underneath its
map tiles (`frontend/js/basemap.js`), so there is a map even on a computer that has never been
online and has no cached tiles.

| File | Natural Earth layer (1:50m) |
|---|---|
| `land.json` | `ne_50m_land` |
| `lakes.json` | `ne_50m_lakes` |
| `borders.json` | `ne_50m_admin_0_boundary_lines_land` |

**Source:** [Natural Earth](https://www.naturalearthdata.com), fetched as GeoJSON from
<https://github.com/nvkelso/natural-earth-vector>. 

**Licence:** public domain. From the Natural Earth terms of use: *"All versions of Natural Earth
raster + vector map data found on this website are in the public domain."* Crediting is not
required (Kepler73 credits it in the map's attribution anyway).

**Processing:** properties removed, coordinates rounded to 2 decimals (about 1 km) and repeated
points dropped, to keep the three files at roughly 1.6 MB. To regenerate or change them run
`python tools/build_basemap.py` (needs internet); nobody has to do that to use Kepler73.
