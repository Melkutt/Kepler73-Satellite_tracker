// Kepler73 - satnogs.js
// SatNOGS lookup dialog: browse the full SatNOGS catalogue with faceted
// filters (modulation / service / type / country) and add matches to the
// active module. Ported from the standalone "Sat Toolkit" SatNOGS Lookup tab.

var _snRows   = null;              // full catalogue rows
var _snTles   = {};               // norad(str) -> [l1, l2]
var _snObs    = null;             // {lat, lon} of the observer, for GEO look angles
var _snUniv   = { mode: [], service: [], type: [], country: [] };
var _snSel    = { mode: new Set(), service: new Set(), type: new Set(), country: new Set() };
var _snSort   = { col: 'norad', rev: false };
var _snWired  = false;
var SN_ROW_CAP = 400;

var _SN_FACET_EL = {
  mode: 'sn-f-mode', service: 'sn-f-service', type: 'sn-f-type', country: 'sn-f-country'
};

function showSatnogsDialog() {
  closeAllMenus();
  openDialog('dlg-satnogs');
  if (!_snWired) {
    _snWired = true;
    ['sn-search', 'sn-fmin', 'sn-fmax'].forEach(function(id) {
      document.getElementById(id).addEventListener('input', snApply);
    });
    ['sn-geo', 'sn-geovis', 'sn-down', 'sn-up', 'sn-active'].forEach(function(id) {
      document.getElementById(id).addEventListener('change', snApply);
    });
    document.querySelectorAll('.sn-table th').forEach(function(th, i) {
      var cols = ['norad', 'name', 'dir', 'mhz', 'mode', 'service', 'type', 'satst', '_geoEl'];
      if (i < cols.length) th.style.cursor = 'pointer';
      if (i < cols.length) th.onclick = function() { snSortBy(cols[i]); };
    });
  }
  if (!_snRows) satnogsFetch(false);
}

async function satnogsFetch(refresh) {
  var st = document.getElementById('sn-status');
  st.textContent = refresh ? 'Re-downloading from SatNOGS…' : 'Loading catalogue…';
  try {
    var res  = await fetch('/api/satnogs/catalog' + (refresh ? '?refresh=1' : ''));
    var data = await res.json();
    if (!res.ok) { st.textContent = data.error || 'Failed to load'; return; }
    _snRows = data.rows;
    _snTles = data.tles || {};

    // Observer position → GEO elevation per row (for the "QTH el" column/filter)
    try {
      var cfg = await (await fetch('/api/config')).json();
      _snObs = { lat: cfg.lat, lon: cfg.lon };
    } catch (e) { _snObs = null; }
    _snRows.forEach(function(r) {
      r._geoEl = (_snObs && r.geo_lon != null)
        ? _snGeoElevation(_snObs.lat, _snObs.lon, r.geo_lon) : null;
    });

    _snUniv = {
      mode:    _snDistinct('mode'),
      service: _snDistinctSplit('service'),
      type:    _snDistinct('type'),
      country: _snDistinctSplit('country')
    };
    _snSel = { mode: new Set(), service: new Set(), type: new Set(), country: new Set() };
    st.textContent = data.counts.rows + ' rows / ' + data.counts.satellites +
      ' satellites · ' + (data.generated || '').slice(0, 10);
    snApply();
  } catch (e) {
    st.textContent = 'Offline – catalogue not available';
  }
}

function _snDistinct(key) {
  var s = new Set();
  _snRows.forEach(function(r) { if (r[key]) s.add(r[key]); });
  return [...s].sort();
}
function _snDistinctSplit(key) {
  var s = new Set();
  _snRows.forEach(function(r) {
    (r[key] || '').split(',').forEach(function(v) { v = v.trim(); if (v) s.add(v); });
  });
  return [...s].sort();
}

// Elevation (deg) of a geostationary satellite at sub-longitude satLon,
// seen from the observer's QTH. Negative = below the horizon.
// Ported from Sat Toolkit's geo_look_angles (ECEF → local ENU).
var _SN_RE = 6378.137, _SN_RGEO = 42164.17;
function _snGeoElevation(obsLat, obsLon, satLon) {
  var lat = obsLat * Math.PI / 180, lon = obsLon * Math.PI / 180;
  var slon = satLon * Math.PI / 180;
  var dx = _SN_RGEO * Math.cos(slon) - _SN_RE * Math.cos(lat) * Math.cos(lon);
  var dy = _SN_RGEO * Math.sin(slon) - _SN_RE * Math.cos(lat) * Math.sin(lon);
  var dz = 0 - _SN_RE * Math.sin(lat);
  var up = dx * Math.cos(lat) * Math.cos(lon)
         + dy * Math.cos(lat) * Math.sin(lon)
         + dz * Math.sin(lat);
  var rng = Math.sqrt(dx * dx + dy * dy + dz * dz);
  return rng > 0 ? Math.asin(up / rng) * 180 / Math.PI : -90;
}

// ── Filtering ───────────────────────────────────────────────

function _snBase(rows) {
  var q  = document.getElementById('sn-search').value.trim().toLowerCase();
  var lo = parseFloat(document.getElementById('sn-fmin').value);
  var hi = parseFloat(document.getElementById('sn-fmax').value);
  var geoMode = document.getElementById('sn-geo').value;        // '' | 'only' | 'hide'
  var geoVis  = document.getElementById('sn-geovis').checked;
  var wantD = document.getElementById('sn-down').checked;
  var wantU = document.getElementById('sn-up').checked;
  var activeOnly = document.getElementById('sn-active').checked;

  return rows.filter(function(r) {
    if (q) {
      var hay = (r.name + ' ' + r.desc + ' ' + r.country + ' ' + r.norad).toLowerCase();
      if (hay.indexOf(q) === -1) return false;
    }
    if (!isNaN(lo) && !(r.mhz != null && r.mhz >= lo)) return false;
    if (!isNaN(hi) && !(r.mhz != null && r.mhz <= hi)) return false;
    if (geoMode === 'only' && r.geo !== 'Yes') return false;
    if (geoMode === 'hide' && r.geo === 'Yes') return false;
    if (geoVis && r.geo === 'Yes' && !(r._geoEl != null && r._geoEl > 0)) return false;
    if (wantD && !wantU && r.dir !== 'Downlink') return false;
    if (wantU && !wantD && r.dir !== 'Uplink') return false;
    if (activeOnly && !(r.txst === 'active' && r.satst !== 'decayed')) return false;
    return true;
  });
}

function _snSelFilter(rows, use) {
  return rows.filter(function(r) {
    if (use.mode && _snSel.mode.size && !_snSel.mode.has(r.mode)) return false;
    if (use.service && _snSel.service.size &&
        !(r.service || '').split(',').some(function(s) { return _snSel.service.has(s.trim()); }))
      return false;
    if (use.type && _snSel.type.size && !_snSel.type.has(r.type)) return false;
    if (use.country && _snSel.country.size &&
        !(r.country || '').split(',').some(function(c) { return _snSel.country.has(c.trim()); }))
      return false;
    return true;
  });
}

function snApply() {
  if (!_snRows) return;
  var base = _snBase(_snRows);

  // Facet availability: apply every OTHER selected facet, collect this facet's values
  var availMode    = new Set(_snSelFilter(base, { service: 1, type: 1, country: 1 }).map(function(r) { return r.mode; }));
  var availType    = new Set(_snSelFilter(base, { mode: 1, service: 1, country: 1 }).map(function(r) { return r.type; }));
  var availService = new Set();
  _snSelFilter(base, { mode: 1, type: 1, country: 1 }).forEach(function(r) {
    (r.service || '').split(',').forEach(function(s) { s = s.trim(); if (s) availService.add(s); });
  });
  var availCountry = new Set();
  _snSelFilter(base, { mode: 1, service: 1, type: 1 }).forEach(function(r) {
    (r.country || '').split(',').forEach(function(c) { c = c.trim(); if (c) availCountry.add(c); });
  });

  _snRenderChips('mode', availMode);
  _snRenderChips('service', availService);
  _snRenderChips('type', availType);
  _snRenderChips('country', availCountry);

  var rows = _snSelFilter(base, { mode: 1, service: 1, type: 1, country: 1 });
  _snRenderTable(rows);
}

function _snRenderChips(facet, avail) {
  var box = document.getElementById(_SN_FACET_EL[facet]);
  box.innerHTML = '';
  var vals = _snUniv[facet].slice().sort(function(a, b) {
    var ax = avail.has(a) ? 0 : 1, bx = avail.has(b) ? 0 : 1;
    return ax - bx || a.localeCompare(b);
  });
  vals.forEach(function(v) {
    var chip = document.createElement('span');
    var sel = _snSel[facet].has(v);
    var off = !avail.has(v) && !sel;
    chip.className = 'sn-chip' + (sel ? ' sel' : '') + (off ? ' off' : '');
    chip.textContent = v;
    chip.onclick = function() {
      if (off) return;                 // unavailable given the other filters
      if (_snSel[facet].has(v)) _snSel[facet].delete(v);
      else _snSel[facet].add(v);
      snApply();
    };
    box.appendChild(chip);
  });
}

function snSortBy(col) {
  if (_snSort.col === col) _snSort.rev = !_snSort.rev;
  else { _snSort.col = col; _snSort.rev = false; }
  snApply();
}

function _snRenderTable(rows) {
  var col = _snSort.col, rev = _snSort.rev ? -1 : 1;
  rows = rows.slice().sort(function(a, b) {
    var x = a[col], y = b[col];
    if (x == null) return 1;
    if (y == null) return -1;
    if (typeof x === 'number') return (x - y) * rev;
    return String(x).localeCompare(String(y)) * rev;
  });

  var existing = new Set(
    Array.from(document.querySelectorAll('.sat-item')).map(function(el) { return el.dataset.norad; })
  );

  var tb = document.getElementById('sn-tbody');
  tb.innerHTML = '';
  var shown = rows.slice(0, SN_ROW_CAP);
  var frag = document.createDocumentFragment();
  shown.forEach(function(r) {
    var tr = document.createElement('tr');
    var added = existing.has(String(r.norad));
    tr.dataset.norad = r.norad;
    if (added) tr.className = 'sn-row-added';
    var dir = r.dir === 'Downlink' ? '↓' : (r.dir === 'Uplink' ? '↑' : '·');
    tr.innerHTML =
      '<td>' + r.norad + '</td>' +
      '<td>' + _snEsc(r.name) + '</td>' +
      '<td>' + dir + '</td>' +
      '<td>' + (r.mhz != null ? r.mhz.toFixed(3) : '—') + '</td>' +
      '<td>' + _snEsc(r.mode) + '</td>' +
      '<td>' + _snEsc(r.service) + '</td>' +
      '<td>' + _snEsc(r.type) + '</td>' +
      '<td>' + _snEsc(r.txst) + '</td>' +
      '<td>' + (r._geoEl != null
                 ? r._geoEl.toFixed(1) + '°'
                 : (r.geo === 'Yes' ? '?' : '—')) + '</td>' +
      '<td class="sn-add"></td>';
    var cell = tr.querySelector('.sn-add');
    if (added) {
      cell.textContent = '✓';
      cell.classList.add('sn-added');
    } else {
      var b = document.createElement('button');
      b.className = 'sn-addbtn';
      b.textContent = '＋';
      b.title = 'Add to active module';
      b.onclick = function() { snAdd(r.norad, r.name, b); };
      cell.appendChild(b);
    }
    frag.appendChild(tr);
  });
  tb.appendChild(frag);

  document.getElementById('sn-count').textContent =
    rows.length + ' matching row' + (rows.length !== 1 ? 's' : '') +
    (rows.length > SN_ROW_CAP ? ' — showing first ' + SN_ROW_CAP + ', narrow the filter' : '');
}

function _snEsc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

async function snAdd(norad, name, btn) {
  if (!_activeModule) {
    document.getElementById('sn-status').textContent = 'No active module – create one first';
    return;
  }
  if (btn) { btn.disabled = true; btn.textContent = '…'; }

  var payload = { norad_id: String(norad), name: name };
  var tle = _snTles[String(norad)];          // SatNOGS's own element set, if any
  if (tle && tle[0] && tle[1]) {
    payload.tle_line1 = tle[0];
    payload.tle_line2 = tle[1];
  }

  try {
    var res = await fetch('/api/modules/' + encodeURIComponent(_activeModule) + '/satellites', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    var data = await res.json();
    if (!res.ok) {
      if (btn) { btn.disabled = false; btn.textContent = '＋'; }
      document.getElementById('sn-status').textContent =
        (data && data.error) ? data.error : ('Add failed (' + res.status + ')');
      return;
    }
    _snMarkAdded(norad);                 // green ✓ + fade on every row with this NORAD
    if (typeof loadSatList === 'function') await loadSatList();
    document.getElementById('sn-status').textContent = '✓ ' + name + ' added to ' + _activeModule;
  } catch (e) {
    if (btn) { btn.disabled = false; btn.textContent = '＋'; }
    document.getElementById('sn-status').textContent = 'Add failed: ' + e.message;
  }
}

// Mark every visible row for this NORAD as "in a module"
function _snMarkAdded(norad) {
  document.querySelectorAll('#sn-tbody tr[data-norad="' + norad + '"]').forEach(function(tr) {
    tr.classList.add('sn-row-added');
    var cell = tr.querySelector('.sn-add');
    if (cell) { cell.innerHTML = ''; cell.textContent = '✓'; cell.classList.add('sn-added'); }
  });
}

function satnogsClear() {
  document.getElementById('sn-search').value = '';
  document.getElementById('sn-fmin').value = '';
  document.getElementById('sn-fmax').value = '';
  document.getElementById('sn-geo').value = '';
  document.getElementById('sn-geovis').checked = false;
  document.getElementById('sn-down').checked = true;
  document.getElementById('sn-up').checked = true;
  document.getElementById('sn-active').checked = false;
  _snSel = { mode: new Set(), service: new Set(), type: new Set(), country: new Set() };
  snApply();
}
