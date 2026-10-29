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

**Windows exe**: `pyinstaller --clean --noconfirm kepler73.spec` (or `build.bat`) →
`dist/Kepler73.exe` (onefile, ~35 MB). `kepler73.spec` bundles `frontend/` as data and
hidden-imports `engineio.async_drivers.threading` + `simple_websocket` (flask-socketio
threading mode). When frozen, `config.BASE_DIR` is `sys._MEIPASS` and `DATA_DIR` is
`~/.kepler73`; `main.py` skips its chdir/sys.path fiddling, and `api/__init__.py` builds
`frontend_dir` from `BASE_DIR`.

- **Python**: 3.11+ (developed on 3.13). Deps in `requirements.txt`: `flask`,
  `flask-socketio`, `sgp4`, `numpy`, `pygame`. Socket.IO runs `async_mode="threading"`
  (no eventlet/gevent).
- Frontend libraries are **vendored** in `frontend/vendor/` (Leaflet 1.9.4 BSD-2, socket.io client
  4.7.2 MIT, licence files + SRI hashes in `frontend/vendor/README.md`) – no CDN, so the page loads
  with no internet. Repo licence: The Unlicense (public domain, `LICENSE`).
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
                        JSON/OMM primary with legacy-TLE fallback; check_online();
                        fetch_group() = ONE-request bulk download of a whole GROUP into
                        data/tle_cache/bulk_<group>.json
  tle_sources.py        parse local element files (TLE/3LE incl. Alpha-5, GP JSON, GP CSV);
                        folder catalog with mtime cache; resolve_tle() dispatches by
                        config["tle_source"] (celestrak | file | file_then_celestrak) and,
                        for the celestrak path, returns whichever of Celestrak / cached
                        SatNOGS catalog has the newest epoch (so a blocked Celestrak can't
                        leave a sat stuck on a months-old element set)
                        Also owns the bulk catalog reader: bulk_find/bulk_search/bulk_info/
                        refresh_bulk (BULK_GROUPS = ("active",), refreshed at most every
                        BULK_REFRESH_SEC = 12 h)
  satnogs.py            per-sat transponder list (7-day cache) + full-catalogue lookup:
                        _build_catalog() joins the satellites/transmitters/tle list
                        endpoints into filterable rows; get_catalog() disk-caches the result
  satcat.py             Celestrak SATCAT decay-status lookup; 30-day disk cache +
                        60 s in-process cache; drives the "decayed" greying
  geocode.py            place-name search (OSM Nominatim) + ground elevation
                        (Open-Meteo); disk-cached, offline-tolerant; powers the
                        Settings location-map picker
  hamlib.py             minimal rigctld TCP client (set/get freq, split, PTT, get_mode, probe);
                        best-effort, never raises
  doppler.py            rx_freq / tx_freq from radial velocity (f_rx = f*(1 - v/c))
  rotctld.py            minimal rotctld TCP client (set_pos/get_pos/stop/probe)
  rotor.py              pure rotator logic: desired_pointing() (elevation gate: park/track) and
                        beyond_deadband(); AOS pre-positioning + north-crossing/flip are TODO
  updatecheck.py        About-dialog "check for updates": latest GitHub release, else the
                        version in main's config.py; refuses in offline mode
  netmode.py            the "offline station" switch: is_offline() is checked before every call
                        to a public internet service (Celestrak, SatNOGS, tiles, Nominatim,
                        Open-Meteo); the user's own TLE sources are NOT blocked
  datapack.py           offline data pack: build_pack() = SatNOGS satellites/transmitters/tle +
                        manifest in one ZIP; install() validates + installs a pack ZIP or a raw
                        SatNOGS API JSON list into data/satnogs_db/; info() = what's there + date
  userdata.py           user-entered data, in their own files so SatNOGS / TLE updates can
                        never overwrite them: manual transponders (data/custom_transponders.json)
                        and satellite notes (data/sat_notes.json); export_all()/import_all()
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
  js/transponder.js     transponder panel / dialog (single-select, manual rows, out-of-band)
  js/hamlib.js          Radio/CAT dialog, status-bar RF/mode chip, CAT strip, band parsing
  js/rotor.js           Rotator dialog, STOP/Park, status-bar chip
  js/basemap.js         built-in offline world map: Natural Earth land/lakes/borders drawn on a canvas
                        pane UNDER the tile layers (3 world copies for the date line); Map menu toggle
  js/offline.js         data-pack export/import, TLE-sources status in Settings, About dialog +
                        update check
  js/userdata.js        notes (📝/❗ badges), manual-transponder editor, export/import of both,
                        the "add satellite: Celestrak or SatNOGS" chooser, toast()
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
- `GET  /api/search?q=` (local file catalog per `tle_source`, then the local bulk Celestrak
  catalog – no request; only a miss falls through to one Celestrak lookup; results capped
  at 300 with `total`)
- `GET  /api/tle/catalog` → `{count, fetched, age_sec, groups}` (bulk catalog status)
- `GET  /api/update-check` → `{current, latest, update_available, source, url, error, offline}`
- `GET/POST /api/rig/config` (host, port, down_hz, up_hz, tx_enabled, deadband_hz, bands, enabled;
  mirrored to `rig_*` keys in app_config), `POST /api/rig/test` (ping + raw probe)
- `GET/POST /api/rotor/config` (`rot_*` keys), `POST /api/rotor/test | park | stop` (park and stop
  also switch tracking off)
- `GET  /api/tle/sources` (status of each line of the TLE-sources setting),
  `POST /api/tle/sources/refresh` (`{spec}` optional: re-download the URL lines, then report)
- `GET  /api/datapack/export[?refresh=1]` (ZIP; `X-Datapack-Info` header), `POST /api/datapack/import`
  (multipart `file=`: a pack ZIP or one raw SatNOGS JSON list), `GET /api/datapack/info`
- `POST /api/tle/import` (multipart `file=` or JSON `{text}` / `{path}`; parses TLE/3LE/
  GP JSON/GP CSV → same result shape as `/api/search` so the search dialog lists them)
- `GET  /api/positions?selected=<name>`
- `GET  /api/passes/<norad_id>` (next pass), `.../from?dt=` and `.../position?jd=`
  (simulator; both use the nearest-epoch element set from the local TLE folder when a
  file source is configured, so historical dates are accurate)
- `GET  /api/satcat/<norad_id>[?refresh=1]` → `{decayed, decay_date, name}`
- `GET  /api/satnogs/catalog[?refresh=1]` → `{rows:[...], tles:{norad:[l1,l2]}, counts}` (SatNOGS lookup)
- `GET  /api/geocode?q=` → `{results:[{name,lat,lon}]}`, `GET /api/elevation?lat=&lon=` → `{elevation}`
- `POST /api/tle/refresh` (updates EVERY module, not just the active one: one bulk catalog
  download, then a local update via `routes.update_all_module_tles()`; honours `tle_source`;
  falls back to the SatNOGS catalog / returns the freshest epoch when Celestrak is blocked), `GET /api/tle/export[?all=1]`
  (3LE `.txt` download — this module, or every module — for Gpredict's local-file update)
- `GET  /api/transmitters/<norad_id>[?refresh=1]` → the user's manual entries first (`manual: true`),
  then the cached SatNOGS list (refreshed lazily when >7 days old, or with `?refresh=1`)
- `POST /api/transmitters/<norad_id>/custom` (add; with `uuid` = update; frequencies in MHz),
  `DELETE /api/transmitters/<norad_id>/custom/<uuid>`
- `GET /api/notes`, `POST /api/notes/<norad_id>` (`{text, flag}`; blank text removes)
- `GET /api/userdata/export` (one JSON: manual transponders + notes), `POST /api/userdata/import`
  (multipart `file=` or JSON; merge, later `updated` stamp wins, nothing deleted)
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
- Version is single-sourced from `config.APP_VERSION` (now **0.4.0**): `main.py` banner,
  `/api/info` and the status bar (`ui.js checkNetworkStatus`) read it; the `User-Agent` headers
  say `Kepler73/0.4` and the static fallbacks in `index.html` (status bar, About) say v0.4 –
  bump all of them on a release (`grep -rn "0\.4"`). `config.GITHUB_REPO` names the repo.
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

## Added (2026-09-06 … 2026-10-30): CAT, rotator, v0.4

**NOT TESTED ON REAL HARDWARE** – only against Hamlib's dummy rig / rotator (`rigctld -m 1`,
`rotctld -m 1`). The README, the About dialog and both dialogs say so; keep saying so until it is.

- **CAT / Doppler** (`hamlib.py`, `doppler.py`, `sockets._update_rig` + `_rig_worker`, `hamlib.js`):
  the 1 Hz loop hands the selected satellite's range-rate to a worker thread that owns all rigctld
  socket I/O (a hung daemon can never stall the position broadcast); it sets the Doppler-corrected RX
  frequency when the change exceeds the deadband (default 10 Hz) and reads back freq + mode/passband
  every 5 s. Status: `rig_status` socket event → status-bar chips (`RF MHz  MODE passband`,
  `CAT ±x kHz`). Sign check: approaching = `v_radial < 0` = RX above nominal.
  TX Doppler/split/PTT code exists (`doppler.tx_freq`, `set_split_freq`, `set_ptt`) but is not wired
  to the UI yet.
- **Transponder panel**: the checkbox is single-select; the selected transponder pushes its
  down/up frequency to the CAT config and drives the Doppler read-out; a CAT strip above the list
  has an on/off toggle. **Radio covers (MHz)** (`rig_bands`, e.g. `144-146, 430-440`; blank = no
  limit) greys out and disables transponders the radio can't reach (display-only).
- **Rotator** (`rotctld.py`, `rotor.py`, `sockets._update_rotor` + `_rotor_worker`, `rotor.js`):
  same worker pattern. Parks below `rot_min_el`, tracks above, deadband per axis (wrap-aware),
  readback every 3 s. STOP / Park disable tracking and send `S` / a park `P`. The dummy rotator
  slews ~6°/s, so the readback visibly lags the command (that is expected).
- **Right panel pin** (📌): docks the panel beside the map (map margin 220 px + Leaflet
  `invalidateSize`), removes the click-catching overlay, hides ✕; state in `localStorage rp_pinned`.
- **About → Check for updates** (`updatecheck.py`): a plain GET to GitHub only when the button is
  pressed; disabled in offline mode. GitHub's release tag is `V0.3.0` (capital V) – `_ver()` copes.
- **Gotchas learned the hard way**
  - `rigctld -r` is the *radio's* device (COM port); the TCP port is `-t` (default 4532). Passing
    `-r 4532` made rigctld try to dial a host called "4532" and every `f` hung.
  - `rigctld -m 1` = dummy rig; isolate a client problem with `rigctl -m 2 -r localhost:4532 f`.
  - Two `python main.py` on port 5000 serve each other's stale cached Jinja template (debug is off,
    so `index.html` is compiled once per process): always make sure exactly one instance runs after
    editing `index.html`.
  - Module names: the file name is the identity (`modules._path`: space→`_`, `/`→`-`), and
    `list_modules()` derives names from file names. A module stored as "SSTV/SSDV" is listed as
    "SSTV-SSDV", and `/api/modules/SSTV%2FSSDV` 404s (Flask decodes `%2F`), so the sidebar showed
    an empty list. Fixed: `GET /api/modules` reports `canonical_name(active)`, and create/rename run
    names through `modules.clean_name()` (path-hostile characters → `-`, empty names rejected).
  - `style.css` and every script carry a `?v=N` buster – bump it, the in-app browser caches hard.

## Screenshots (docs/img)

`01`–`03` are the user's own (PNG). `04`–`09` (JPG, 1600×1000) were taken from a throw-away **demo
copy** of the app with neutral data (Stockholm as QTH, a curated module, Hamlib dummy rig/rotor on
ports 4632/4633, a fake LAN TLE server on :8765) driven through headless Chrome over the DevTools
protocol – never from the user's own `data/`, so no personal location, modules or notes end up on
GitHub. Retake them the same way if the UI changes.

## Added (2026-10-29)

- **Manual transponders.** `+` in the transponder panel opens an editor (description, downlink/
  uplink + optional upper edge of a linear passband, mode, baud, inverting, active, note).
  Stored in `data/custom_transponders.json`, merged in at read time, shown first with a
  `✎ manual` tag + a dashed border + an edit button; the detail dialog says it is not SatNOGS-
  verified. A SatNOGS refresh rewrites only `data/transponders/<id>.json`, so they survive it.
  All user text is escaped before it goes into innerHTML (an imported file can carry anything).
- **Satellite notes.** Per NORAD id (not per module) in `data/sat_notes.json`; 📝 (or ❗ when
  flagged) next to the name in the list opens the editor; the right panel shows the note.
- **Export / import** of both via the Satellites menu (`kepler73_userdata.json`) – for moving to
  another computer.
- **List sorted by next pass** (`sidebar.js sortSatList`): above the horizon now → soonest AOS →
  no pass data → decayed. Re-sorted (debounced) when a countdown arrives or a satellite rises/sets.
- **New satellites get the alarm OFF** (`modules.add_satellite`); the list has `Alarm` / `Track`
  column labels with tooltips, and the About dialog explains them.
- The sidebar `+` opens a chooser: Celestrak (local catalog search / TLE file) or SatNOGS lookup.

## Added (2026-10-30) – offline station

The target is a station that NEVER gets internet: element sets arrive from a LAN server / share,
SatNOGS data by USB stick about once a year.

- **TLE sources = several lines** in the existing `tle_file` setting (like Gpredict's server list):
  each line is a file, a folder (also a network share) or an `http(s)://` URL. URL lines are
  downloaded on an explicit refresh only (Settings "Fetch / check", `/api/tle/refresh`, the 6 h
  sweep) – never while searching – and cached as parsed records in `data/tle_cache/sources/`, so
  they keep working while the server is down. Merging: newest epoch wins per satellite
  (`tle_sources.load_file_catalog`). A single plain path behaves exactly as before. SatNOGS's own
  `/api/tle/` JSON (`{tle0,tle1,tle2,...}`) is now understood by every TLE import.
  Modes: `file` = "My TLE sources only (offline)", `file_then_celestrak` = sources first.
- **Offline data pack.** On a computer with internet: Satellites → Export offline data pack (3
  SatNOGS lists + manifest, ~0.8 MB zip). On the station: Import offline data pack – also accepts
  one raw SatNOGS API JSON file (kind auto-detected; a paginated `{results:[…]}` answer is fine).
  Validation: manifest + format number, only the three known member names (nothing is extracted
  to a path from the zip), per-member size caps read with a hard limit, `norad_cat_id` shape check.
  After install `catalog.json` and the per-satellite transponder caches are dropped and the
  catalog is rebuilt with no network. `satnogs.get_transponders()` falls back to the full
  `transmitters.json` for satellites without a per-satellite cache, so every transponder list works
  offline. The SatNOGS lookup status line shows the date of the SatNOGS data.
- **Offline station switch** (Settings; config `offline_mode`; `netmode.is_offline()`): short-circuits
  every call to Celestrak (reuses `celestrak.is_blocked()`), SatNOGS, geocode/elevation, the tile
  upstream (cached tiles still served, else an empty tile) and the connectivity check; the status
  dot reads OFFLINE MODE. TLE sources are deliberately exempt.
- **Built-in world map** (`frontend/vendor/naturalearth/{land,lakes,borders}.json`, Natural Earth 1:50m,
  public domain, ~1.6 MB, rebuilt by `tools/build_basemap.py`): drawn by `basemap.js` below the tiles. An
  uncached tile is a 1x1 transparent PNG (offline `_fetch_tile` returns None), so the vector map shows
  through; a fresh station used to show a blank map. Detailed tiles still need a copied `tile_cache`.
- **Page needs no internet:** Leaflet and the socket.io client are vendored under `frontend/vendor/`
  (done 2026-10-30; the PyInstaller spec bundles `frontend/` so the exe has them too). Map tiles still
  exist offline only for areas browsed before (copy `data/tile_cache/` to the station).

## Performance notes

- `/api/satcat/<id>` answers from cache only and kicks the network fetch to a
  background thread (`satcat.refresh_async`, one attempt per id per 5 min). A blocking
  SATCAT call is a 10 s timeout whenever Celestrak is unreachable, and the sidebar hits
  this endpoint once per satellite on every list reload.
- `/api/passes/<id>` results are memoised for 120 s per (satellite, observer) in
  `routes._pass_cache`, cleared by `_reload_records()`. The sidebar refetches every
  satellite's next pass on each list reload and again every 5 min.
- **Being gentle to Celestrak – bulk catalog.** `celestrak.fetch_group("active")` mirrors the
  whole `GROUP=active` set (~17k objects, ~4 MB, ONE request) into
  `data/tle_cache/bulk_active.json`, refreshed at most every `BULK_REFRESH_SEC` (12 h).
  Search and "Update all TLEs" read that file, so Celestrak requests no longer scale with the
  number of satellites or modules. `sockets._auto_update_tle()` (every `TLE_UPDATE_INTERVAL`,
  6 h) refreshes the bulk file if stale, then calls `routes.update_all_module_tles(only_stale=True)`
  for ALL modules (each distinct satellite resolved once, applied everywhere, never
  downgraded, modules saved to disk).
- **Satellites outside the bulk catalog** (old derelicts, decommissioned – e.g. the `136MHz`
  module) still need a per-satellite request. Policy in `update_all_module_tles()`: skip
  decayed ones (SATCAT cache), only ask when their elements are older than
  `OFFCATALOG_STALE_SEC` (3 d) AND not attempted within that time (persisted in
  `data/tle_cache/offcatalog_attempts.json`), spaced `OFFCATALOG_SPACING` (0.6 s) apart.
  Verified on the real modules: 14 of 37 satellites are off-catalog; 1st manual refresh = 14
  requests, every later refresh/sweep = 0.
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
