# Kepler73

Satellite tracker for amateur-radio use. Author: **SA1CKW**. Public repo:
`github.com/Melkutt/Kepler73-Satellite_tracker`.
Code, comments and UI strings are **English only**.

A local Flask + Socket.IO server that serves a single-page web UI: world map with live
satellite positions, ground tracks, footprints, a polar/radar plot of the next pass,
transponder/frequency info, pass prediction, a pass simulator, and audible pass alarms.

## Run / develop

```bash
python main.py
```

Starts the server on `http://127.0.0.1:5000` and opens the default browser after ~1.5 s.
No build step for the frontend (plain HTML/CSS/vanilla JS).

- **Python**: 3.11+ (developed on 3.13). Deps in `requirements.txt`: `flask`,
  `flask-socketio`, `sgp4`, `numpy`, `pygame`. Socket.IO runs `async_mode="threading"`
  (no eventlet/gevent).
- Frontend CDN deps loaded in `frontend/index.html`: Leaflet 1.9.4, socket.io client 4.7.2.
- `data/` is git-ignored (holds user config, modules, and the TLE / transponder / map-tile
  caches).

## Architecture

```
main.py                 entrypoint: sets cwd, starts socketio.run(create_app())
api/
  __init__.py           Flask app factory; serves frontend/ as static root; inits SocketIO;
                        starts the background broadcast thread
  routes.py             all REST endpoints + shared in-memory `state` dict + OSM/Esri tile proxy
  sockets.py            background thread: emits positions every 1 s, checks alarms;
                        Socket.IO event handlers for selection / alarm settings
backend/
  sgp4_engine.py        orbital math: SGP4 propagation, ECI<->geodetic, GMST, topocentric
                        az/el/range, ground track, footprint, Doppler radial velocity,
                        next-pass search (coarse step + bisection). Pure functions.
  celestrak.py          fetch TLE/OMM from Celestrak by NORAD id or name; local .tle cache;
                        JSON/OMM primary with legacy-TLE fallback; check_online()
  tle_sources.py        parse local element files (TLE/3LE incl. Alpha-5, GP JSON, GP CSV);
                        folder catalog with mtime cache; resolve_tle() dispatches by
                        config["tle_source"] (celestrak | file | file_then_celestrak) and,
                        for the celestrak path, returns whichever of Celestrak / cached
                        SatNOGS catalog has the newest epoch (so a blocked Celestrak can't
                        leave a sat stuck on a months-old element set)
  satnogs.py            per-sat transponder list (7-day cache) + full-catalogue lookup:
                        _build_catalog() joins the satellites/transmitters/tle list
                        endpoints into filterable rows; get_catalog() disk-caches the result
  satcat.py             Celestrak SATCAT decay-status lookup; 30-day disk cache +
                        60 s in-process cache; drives the "decayed" greying
  geocode.py            place-name search (OSM Nominatim) + ground elevation
                        (Open-Meteo); disk-cached, offline-tolerant; powers the
                        Settings location-map picker
  modules.py            "modules" = named satellite groups, one JSON file each in data/modules/;
                        also load/save of data/app_config.json
  alarm.py              pygame-synthesised alarm sounds (CW "AOS" morse, beeps, rising tone);
                        check_alarms() decides when a pass warning fires
frontend/
  index.html            layout, dialogs, script includes
  js/map.js             Leaflet map, satellite markers, tracks, footprints
  js/polarmap.js        canvas polar/radar plot of a pass
  js/sidebar.js         module + satellite list, selection
  js/simulator.js       pass simulator: scrub any pass +/- 7 days
  js/transponder.js     transponder panel / dialog
  js/satnogs.js         SatNOGS lookup dialog: faceted filter over the full catalogue
  js/ui.js              menus, settings dialog, search dialog, alarm banner
  js/websocket.js       socket.io client wiring
data/                   (git-ignored) app_config.json, modules/*.json, tle_cache/,
                        transponders/, satcat/, geocode/, satnogs_db/,
                        tile_cache/{osm,satellite}/z/x/y.png
```

### Shared state
`api/routes.py` defines a module-level `state` dict (`active_module`, `sat_records`,
`config`, `online`). `api/sockets.py` imports it. `sat_records` maps **norad_id -> sgp4
Satrec**. It is rebuilt by `_reload_records()` whenever the active module or its TLEs change.

### Request flow for live positions
`sockets._loop` (1 Hz) -> `routes._compute_positions()` -> for each sat: `propagate()` +
`eci_to_azel()` + `forward_track()` -> `socketio.emit("satellite_positions", ...)` ->
`frontend/js/websocket.js` -> `map.js`.

### Key REST endpoints (see `api/routes.py`)
- `GET  /api/info`, `GET/POST /api/config`
- `GET/POST /api/modules`, `GET/POST/DELETE /api/modules/<name>`,
  `POST /api/modules/<name>/activate|rename`
- `POST/DELETE /api/modules/<name>/satellites[/<norad_id>]`,
  `POST .../satellites/<norad_id>/alarm` — POST resolves a TLE via `tle_sources.resolve_tle`
  when the body has no `tle_line1/2` (e.g. adding from the SatNOGS lookup)
- `GET  /api/search?q=` (Celestrak name/NORAD id, or a local file catalog per `tle_source`)
- `POST /api/tle/import` (multipart `file=` or JSON `{text}` / `{path}`; parses TLE/3LE/
  GP JSON/GP CSV → same result shape as `/api/search` so the search dialog lists them)
- `GET  /api/positions?selected=<name>`
- `GET  /api/passes/<norad_id>` (next pass), `.../from?dt=` and `.../position?jd=`
  (simulator; both use the nearest-epoch element set from the local TLE folder when a
  file source is configured, so historical dates are accurate)
- `GET  /api/satcat/<norad_id>[?refresh=1]` → `{decayed, decay_date, name}`
- `GET  /api/satnogs/catalog[?refresh=1]` → `{rows:[...], tles:{norad:[l1,l2]}, counts}` (SatNOGS lookup)
- `GET  /api/geocode?q=` → `{results:[{name,lat,lon}]}`, `GET /api/elevation?lat=&lon=` → `{elevation}`
- `POST /api/tle/refresh` (honours `tle_source`; falls back to the SatNOGS catalog and
  returns the freshest epoch when Celestrak is blocked), `GET /api/tle/export[?all=1]`
  (3LE `.txt` download — this module, or every module — for Gpredict's local-file update)
- `GET  /api/transmitters/<norad_id>[?refresh=1]`
- `POST /api/heartbeat` (UI keep-alive; stale heartbeat mutes alarms), `POST /api/quit`
- `GET  /tiles/<layer>/<z>/<x>/<y>.png` proxy+cache; `layer` is `osm` or `satellite` (Esri)

## Conventions

- **English only** in code, comments, docstrings and user-facing strings.
- Backend math functions are pure and take/return plain numbers or small tuples;
  angles in degrees at API boundaries, radians internally in `sgp4_engine.py`.
- Times are Julian Date (`now_jd()`), UTC only.
- New frontend JS: add a `<script src="/js/x.js?v=N">` line to `index.html` and bump the
  `?v=` query when changing cached JS.
- Observer location / min elevation / alarm settings live in `data/app_config.json`
  via `backend/modules.load_config()/save_config()`. Keys added over time: `tle_source`
  (`celestrak` | `file` | `file_then_celestrak`) and `tle_file` (a file or folder path).
- TLE-format parsing (catalog numbers etc.) is future-proofed via `backend/tle_sources.py`:
  prefer the OMM/GP JSON+CSV path; classic TLE parsing decodes Alpha-5 catalog numbers.
  See https://celestrak.org/NORAD/documentation/gp-data-formats.php

## Fixed (2026-09-01)

- `TLE_UPDATE_INTERVAL` is now defined in `backend/config.py` (3600 s) and imported by
  `api/sockets.py`. Previously undefined → `NameError` every tick, silently swallowed, which
  also prevented the alarm check below it from ever running.
- `api/sockets.py:_auto_update_tle()` now keys `sat_records` by `norad_id` (was `sat["name"]`),
  matching `routes._reload_records()` / `_compute_positions()`.
- The broadcast loop's catch-all now `traceback.print_exc()` instead of `pass`.
- Version is single-sourced from `config.APP_VERSION` (now **0.3.0**): `main.py` banner and
  `/api/info` read it; `index.html` status bar and all `User-Agent` headers say `Kepler73/0.3`.
- `frontend/js/ui.js` gained the `quitApp()` function; a Quit control sits both in the
  Settings menu and as a button in the 30px status bar (bottom-right).
- Removed the orphan duplicate `id="dlg-transponder"` block in `index.html` (the live one
  the JS drives uses `tx-dlg-title` / `tx-dlg-content`).
- Editor `settings.json` moved from repo root to `.vscode/settings.json` (git-ignored).

## Added (2026-09-01)

- **Local / offline TLE source.** `backend/tle_sources.py` parses TLE, 3LE, Celestrak GP
  JSON and GP CSV from a file or a folder. Settings dialog has a "TLE source" dropdown +
  file/folder path; `POST /api/tle/import` (upload or paste) feeds the search dialog's
  result list. In `file` mode nothing hits the network (search, refresh, auto-update).
- `celestrak.search_tle()` now turns a Celestrak HTTP 404 into "no current elements
  (unknown catalog number or decayed object)" instead of a raw `HTTPError`.
- **Decayed-object rows.** When a NORAD-id search has no current elements,
  `celestrak.lookup_satcat()` queries the Celestrak SATCAT; `/api/search` then returns a
  non-addable row `{unavailable: true, reason: "re-entered <date>"}` that the search dialog
  renders greyed out / struck through instead of just showing an error.
- **Decayed satellites in the sidebar.** `backend/satcat.py` + `/api/satcat/<id>`;
  `frontend/js/sidebar.js` greys out / strikes through decayed sats and drops their
  countdown, and `routes._compute_positions()` skips them (cache-only check, no per-tick
  network) so they get no map marker.
- **Pass simulator uses a real date picker** (`<input type="date">`, 1 year back / 1 month
  forward) instead of a +/-7-day dropdown. For a historical date to be accurate the
  simulator needs a period-appropriate element set: point the local TLE source at a folder
  of dated TLE files and `tle_sources.find_in_file_near()` picks the nearest epoch per
  request. `/api/passes/<id>/from` reports `tle_epoch_used`; the UI warns when it is
  >10 days from the simulated date.
- **Observer-location map picker** in the Settings dialog (`backend/geocode.py`,
  `/api/geocode`, `/api/elevation`, `frontend/js/ui.js` `_ensureLocMap`/`locSearch`): a
  Leaflet mini-map on the `/tiles/osm` proxy; search a place name, click/drag the pin to
  set lat/lon, altitude is auto-filled from Open-Meteo. Writes into the existing
  `cfg-lat`/`cfg-lon`/`cfg-alt` fields, so `saveSettings()` is unchanged.
- **Follow / lock-map checkbox** per satellite in the sidebar (`.follow-check`): one at a
  time; `sidebar.js` tracks `_followSat` and calls `map.js` `setFollowSat()`, which
  `panTo`s the followed satellite on every `satellite_positions` update.
- **SatNOGS lookup dialog** (Satellites menu → 🔎, `frontend/js/satnogs.js`,
  `backend/satnogs.py` `get_catalog()`, `/api/satnogs/catalog`). Ported from the standalone
  "Sat Toolkit". Downloads the SatNOGS satellites/transmitters/tle lists once (7-day cache),
  builds one row per transmitter direction, and filters client-side: text, freq min/max,
  Geo (all / only / hide), "GEO above my horizon", downlink/uplink, active-only, plus
  faceted chip groups (modulation / service / type / country) that grey out zero-match
  values. `_build_catalog()` adds `geo_lon` (sub-satellite longitude via one SGP4 propagate)
  for geostationary objects; `satnogs.js` `_snGeoElevation()` turns that + the observer QTH
  into the "QTH el" column (negative = below the horizon). Match count is in the bottom-left
  footer. Each row has a ＋ that adds to the active module (TLE from `catalog_tle()` else
  resolved server-side); adding one row marks every same-NORAD row (`_snMarkAdded`) with a
  green ✓ and fades it.

## Performance notes

- `/api/satcat/<id>` answers from cache only and kicks the network fetch to a
  background thread (`satcat.refresh_async`, one attempt per id per 5 min). A blocking
  SATCAT call is a 10 s timeout whenever Celestrak is unreachable, and the sidebar hits
  this endpoint once per satellite on every list reload.
- `/api/passes/<id>` results are memoised for 120 s per (satellite, observer) in
  `routes._pass_cache`, cleared by `_reload_records()`. The sidebar refetches every
  satellite's next pass on each list reload and again every 5 min.
- **Being gentle to Celestrak.** `sockets._auto_update_tle()` sweeps every
  `TLE_UPDATE_INTERVAL` (6 h) and only re-fetches element sets already older than
  `TLE_STALE_SEC` (18 h) → ~1 request per satellite per day, none on a fresh module.
- Both `_auto_update_tle` and `POST /api/tle/refresh` do one up-front `celestrak.celestrak_up()`
  check (4 s) and pass `allow_celestrak` to `tle_sources.resolve_tle()`; when Celestrak is
  down/blocked they go straight to the cached SatNOGS catalog instead of eating a ~20 s
  timeout per satellite. `resolve_tle` returns whichever of Celestrak / SatNOGS has the
  newest epoch.
- **Manual refresh is rate-gated.** `POST /api/tle/refresh` returns `429 {cooldown:true,
  wait_minutes}` if the last manual refresh (`config["tle_last_refresh"]`) was under
  `TLE_MANUAL_COOLDOWN_SEC` (2 h) ago and the source can hit Celestrak; the UI asks the user
  to confirm, then re-POSTs `{force:true}`.
- `celestrak.py` detects a rate-limit block (HTTP 403/429 or a "blocked/rate limit/abuse"
  body), then `is_blocked()` short-circuits every Celestrak call for `CELESTRAK_BACKOFF_SEC`
  (8 h); `/api/info` reports `celestrak_blocked_until` and the status bar shows a notice.
  A bulk `?GROUP=…&FORMAT=tle` fetch would be even friendlier (not yet done).
- `sidebar.js` guards `loadSatList()` / `activateModule()` with a `_modGen` counter so a
  fast module switch cannot render the previous module's satellites; `loadSatList()` always
  clears `#sat-list` once its fetch resolves (or shows a retry hint) rather than leaving a
  stale list.
- `GET /` sends `Cache-Control: no-cache` – `index.html` has no `?v=` buster, so without
  this the browser can keep serving a stale page (old `?v=` refs → old JS) after an update,
  which shows up as half-applied UI (missing buttons, wrong sidebar).
- `sgp4_engine.find_next_pass()` scans at a 20 s coarse step (was 15 s); AOS/LOS are still
  bisected to sub-second, `max_el` can be ~0.3° coarse.

## Known issues / gotchas

- `allow_unsafe_werkzeug=True` in `main.py` — fine for localhost single-user, not for
  exposure.
- Tile proxy has no upstream rate-limiting; OSM usage policy expects a low request rate.
- `backend/satnogs.py._parse_transponders()` defines a nested `fmt_freq()` that is unused
  (only `fmt_range()` is called).

## External services

- **Celestrak** `celestrak.org/NORAD/elements/gp.php` (elements) + `/satcat/records.php` (decay).
- **SatNOGS DB** `db.satnogs.org/api/transmitters/` — transponder frequencies/modes.
- **OpenStreetMap** tiles + **Esri World Imagery** tiles — via the `/tiles/...` proxy.
- **Nominatim** `nominatim.openstreetmap.org` — place-name geocoding (location picker).
- **Open-Meteo** `api.open-meteo.com/v1/elevation` — ground elevation (location picker).

All are cached under `data/`, so the app degrades to offline use with stale data.
