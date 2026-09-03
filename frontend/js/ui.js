// Kepler73 - ui.js
// Dialogs, menus, search and settings

// Show debris satellites in search results (default: off)
var _showDebris = localStorage.getItem('showDebris') === '1';

// ── Menus ────────────────────────────────────────────────────

function toggleMenu(id) {
  var el     = document.getElementById(id);
  var isOpen = el.classList.contains('open');
  closeAllMenus();
  if (!isOpen) el.classList.add('open');
}

function closeAllMenus() {
  document.querySelectorAll('.dropdown').forEach(function(d) {
    d.classList.remove('open');
  });
}

// Close menus when clicking outside
document.addEventListener('click', function(e) {
  if (!e.target.closest('.menu-group')) closeAllMenus();
});

// ── Dialogs ──────────────────────────────────────────────────

function openDialog(id) {
  document.getElementById(id).classList.add('open');
}

function closeDialog(id) {
  document.getElementById(id).classList.remove('open');
}

// Close dialog when clicking background
document.querySelectorAll('.modal-bg').forEach(function(bg) {
  bg.addEventListener('click', function(e) {
    if (e.target === bg) bg.classList.remove('open');
  });
});

// ── New module ───────────────────────────────────────────────

function showNewModuleDialog() {
  document.getElementById('new-module-name').value = '';
  openDialog('dlg-new-module');
  setTimeout(function() { document.getElementById('new-module-name').focus(); }, 100);
  closeAllMenus();
}

async function createModule() {
  var name = document.getElementById('new-module-name').value.trim();
  if (!name) return;
  var res = await fetch('/api/modules', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: name })
  });
  if (res.ok) {
    closeDialog('dlg-new-module');
    await loadModuleList();
    await loadSatList();
  } else {
    var d = await res.json();
    alert(d.error || 'Error creating module');
  }
}

// ── Rename module ────────────────────────────────────────────

function showRenameDialog() {
  document.getElementById('rename-input').value = _activeModule || '';
  openDialog('dlg-rename');
  setTimeout(function() { document.getElementById('rename-input').focus(); }, 100);
  closeAllMenus();
}

async function renameModule() {
  var newName = document.getElementById('rename-input').value.trim();
  if (!newName || !_activeModule) return;
  await fetch('/api/modules/' + encodeURIComponent(_activeModule) + '/rename', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: newName })
  });
  closeDialog('dlg-rename');
  await loadModuleList();
  await loadSatList();
}

// ── Delete module ────────────────────────────────────────────

async function deleteActiveModule() {
  if (!_activeModule) return;
  if (!confirm('Permanently delete module \'' + _activeModule + '\'?')) return;
  await fetch('/api/modules/' + encodeURIComponent(_activeModule), { method: 'DELETE' });
  closeAllMenus();
  await loadModuleList();
  await loadSatList();
}

// ── Search satellite ─────────────────────────────────────────

function showSearchDialog() {
  document.getElementById('search-input').value        = '';
  document.getElementById('search-results').innerHTML  = '';
  document.getElementById('search-status').textContent = '';
  var paste = document.getElementById('tle-paste');
  var file  = document.getElementById('tle-file-input');
  if (paste) paste.value = '';
  if (file)  file.value  = '';
  _searchResults     = [];
  _selectedSearchIdx = -1;
  openDialog('dlg-search');
  setTimeout(function() { document.getElementById('search-input').focus(); }, 100);
  closeAllMenus();
}

// Render a list of {name, norad_id, tle_line1, tle_line2, tle_epoch} into the
// results box. Shared by Celestrak search and file/paste import.
function renderSearchResults(list) {
  var status  = document.getElementById('search-status');
  var results = document.getElementById('search-results');
  results.innerHTML  = '';
  _selectedSearchIdx = -1;

  var filtered = _showDebris
    ? list
    : list.filter(function(s) { return !s.name.endsWith(' DEB'); });
  _searchResults = filtered;

  status.textContent = filtered.length + ' result' +
    (filtered.length !== 1 ? 's' : '') +
    (filtered.length ? ' – click or double-click to select' : '');

  var existingIds = new Set(
    Array.from(document.querySelectorAll('.sat-item')).map(function(el) {
      return el.dataset.norad;
    })
  );

  filtered.forEach(function(sat, i) {
    var already = existingIds.has(sat.norad_id);
    var dead    = !!sat.unavailable;
    var div = document.createElement('div');
    div.className = 'search-item'
      + (dead ? ' unavailable' : (already ? ' already-added' : ''));
    var right = dead ? '↓ ' + (sat.reason || 'decayed')
                     : (already ? '✓ added' : (sat.tle_epoch || ''));
    div.innerHTML = '<span class="norad">' + sat.norad_id + '</span>'
      + '<span>' + sat.name + '</span>'
      + '<span class="epoch">' + right + '</span>';
    if (!already && !dead) {
      div.onclick    = function() { selectSearchResult(i); };
      div.ondblclick = function() { selectSearchResult(i); addSelectedSat(); };
    }
    results.appendChild(div);
  });
}

async function doSearch() {
  var q = document.getElementById('search-input').value.trim();
  if (!q) return;

  var status = document.getElementById('search-status');
  status.textContent = 'Searching...';
  document.getElementById('search-results').innerHTML = '';
  _searchResults = [];

  try {
    var res  = await fetch('/api/search?q=' + encodeURIComponent(q));
    var data = await res.json();
    if (!res.ok) {
      status.textContent = 'Error: ' + data.error;
      return;
    }
    renderSearchResults(data.results);
  } catch(e) {
    status.textContent = 'Network error: ' + e.message;
  }
}

async function importTleFile() {
  var fileEl = document.getElementById('tle-file-input');
  var file   = fileEl && fileEl.files[0];
  var text   = (document.getElementById('tle-paste').value || '').trim();
  var status = document.getElementById('search-status');

  if (!file && !text) {
    status.textContent = 'Choose a file or paste element sets first';
    return;
  }

  status.textContent = 'Parsing...';
  document.getElementById('search-results').innerHTML = '';
  _searchResults = [];

  try {
    var res;
    if (file) {
      var fd = new FormData();
      fd.append('file', file);
      res = await fetch('/api/tle/import', { method: 'POST', body: fd });
    } else {
      res = await fetch('/api/tle/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: text })
      });
    }
    var data = await res.json();
    if (!res.ok) {
      status.textContent = 'Error: ' + data.error;
      return;
    }
    renderSearchResults(data.results);
  } catch(e) {
    status.textContent = 'Import failed: ' + e.message;
  }
}

function selectSearchResult(idx) {
  _selectedSearchIdx = idx;
  document.querySelectorAll('.search-item').forEach(function(el, i) {
    el.classList.toggle('selected', i === idx);
  });
}

async function addSelectedSat() {
  if (_selectedSearchIdx < 0 || !_activeModule) return;
  var sat = _searchResults[_selectedSearchIdx];
  if (!sat || sat.unavailable) return;
  var res = await fetch('/api/modules/' + encodeURIComponent(_activeModule) + '/satellites', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(sat)
  });
  if (res.ok) {
    var status = document.getElementById('search-status');
    status.textContent = '✓ ' + sat.name + ' added';
    status.style.color = 'var(--accent)';
    setTimeout(function() {
      status.textContent = '';
      status.style.color = '';
    }, 2000);
    _selectedSearchIdx = -1;
    await loadSatList();
    refreshSearchGraying();
  }
}

function refreshSearchGraying() {
  var existingIds = new Set(
    Array.from(document.querySelectorAll('.sat-item')).map(function(el) {
      return el.dataset.norad;
    })
  );
  document.querySelectorAll('.search-item').forEach(function(el, i) {
    var norad = el.querySelector('.norad').textContent;
    var already = existingIds.has(norad);
    el.classList.toggle('already-added', already);
    var epoch = el.querySelector('.epoch');
    if (already) {
      epoch.textContent = '✓ added';
      el.onclick    = null;
      el.ondblclick = null;
    }
  });
}

// ── Remove satellite ─────────────────────────────────────────

async function removeSelectedSat() {
  if (!_selectedSat || !_activeModule) return;
  var item = document.querySelector('.sat-item[data-name="' + _selectedSat + '"]');
  if (!item) return;
  if (!confirm('Remove \'' + _selectedSat + '\' from module?')) return;
  await fetch('/api/modules/' + encodeURIComponent(_activeModule) + '/satellites/' + item.dataset.norad,
    { method: 'DELETE' });
  _selectedSat = null;
  closeAllMenus();
  await loadSatList();
}

// ── Update all TLEs ──────────────────────────────────────────

function exportTLE(allModules) {
  closeAllMenus();
  // Content-Disposition: attachment on the response → browser downloads it,
  // the page is not navigated away from.
  window.location.href = '/api/tle/export' + (allModules ? '?all=1' : '');
}

async function refreshAllTLE(force) {
  closeAllMenus();
  var sb = document.getElementById('statusbar');
  var orig = sb ? sb.innerHTML : '';
  if (sb) sb.innerHTML = '<span style="color:var(--warn)">⟳ Updating TLEs...</span>';

  try {
    var res  = await fetch('/api/tle/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ force: !!force })
    });
    var data = await res.json();

    if (data.cooldown) {
      if (sb) sb.innerHTML = orig;
      var ok = confirm(
        'It has only been ' + data.last_refresh_min_ago + ' min since the last TLE update.\n\n' +
        'Celestrak temporarily blocks IPs that fetch more often than the data ' +
        'changes (~every 2 h). Wait ~' + data.wait_minutes + ' min, or update anyway ' +
        'and risk a block?\n\nUpdate now anyway?');
      if (ok) return refreshAllTLE(true);
      return;
    }
    if (data.error) {
      if (sb) sb.innerHTML = orig;
      alert('TLE update failed: ' + data.error);
    } else {
      var msg = '✓ TLEs updated: ' + data.updated + '/' + data.total;
      if (data.failed && data.failed.length) {
        msg += '  Failed: ' + data.failed.join(', ');
      }
      if (data.note) msg += '  – ' + data.note;
      if (sb) {
        sb.innerHTML = '<span style="color:var(--accent)">' + msg + '</span>';
        setTimeout(function() { sb.innerHTML = orig; }, 5000);
      } else {
        alert(msg);
      }
    }
  } catch(e) {
    if (sb) sb.innerHTML = orig;
    alert('TLE update failed: ' + e.message);
  }
}

// ── Settings ─────────────────────────────────────────────────

async function showSettingsDialog() {
  var res = await fetch('/api/config');
  var cfg = await res.json();
  document.getElementById('cfg-lat').value         = cfg.lat;
  document.getElementById('cfg-lon').value         = cfg.lon;
  document.getElementById('cfg-alt').value         = cfg.alt;
  document.getElementById('cfg-min-el').value      = cfg.min_el;
  document.getElementById('cfg-alarm-min').value   = cfg.alarm_min || 3;
  document.getElementById('cfg-alarm-sound').value = cfg.alarm_sound || 'cw_aos';
  document.getElementById('cfg-show-deb').checked  = _showDebris;
  document.getElementById('cfg-tle-source').value  = cfg.tle_source || 'celestrak';
  document.getElementById('cfg-tle-file').value    = cfg.tle_file || '';
  document.getElementById('loc-search').value          = '';
  document.getElementById('loc-search-results').innerHTML = '';
  openDialog('dlg-settings');
  closeAllMenus();
  setTimeout(function() {
    _ensureLocMap();
    var la = parseFloat(cfg.lat), lo = parseFloat(cfg.lon);
    if (!isNaN(la) && !isNaN(lo)) {
      _locMap.setView([la, lo], 11);
      _locMarker.setLatLng([la, lo]);
    }
    _locMap.invalidateSize();
  }, 150);
}

// ── Observer-location map picker ─────────────────────────────

var _locMap = null, _locMarker = null;

function _ensureLocMap() {
  if (_locMap) return;
  _locMap = L.map('loc-map', { zoomControl: true, attributionControl: false })
    .setView([59.33, 18.07], 6);
  L.tileLayer('/tiles/osm/{z}/{x}/{y}.png', { maxZoom: 19 }).addTo(_locMap);
  _locMarker = L.marker([59.33, 18.07], { draggable: true }).addTo(_locMap);

  _locMap.on('click', function(e) {
    _locSetPoint(e.latlng.lat, e.latlng.lng, false);
  });
  _locMarker.on('dragend', function() {
    var p = _locMarker.getLatLng();
    _locSetPoint(p.lat, p.lng, false);
  });
}

// Apply a picked position to the lat/lon fields and look up altitude.
function _locSetPoint(lat, lon, recenter) {
  document.getElementById('cfg-lat').value = lat.toFixed(5);
  document.getElementById('cfg-lon').value = lon.toFixed(5);
  if (_locMarker) _locMarker.setLatLng([lat, lon]);
  if (recenter && _locMap) _locMap.setView([lat, lon], Math.max(_locMap.getZoom(), 12));

  var altEl = document.getElementById('cfg-alt');
  altEl.classList.add('looking-up');
  fetch('/api/elevation?lat=' + lat.toFixed(5) + '&lon=' + lon.toFixed(5))
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (typeof d.elevation === 'number') altEl.value = Math.round(d.elevation);
    })
    .catch(function() {})
    .finally(function() { altEl.classList.remove('looking-up'); });
}

async function locSearch() {
  var q = document.getElementById('loc-search').value.trim();
  var box = document.getElementById('loc-search-results');
  if (!q) return;
  box.innerHTML = '<span class="hint">Searching…</span>';
  try {
    var res  = await fetch('/api/geocode?q=' + encodeURIComponent(q));
    var data = await res.json();
    if (!res.ok) { box.innerHTML = '<span class="hint">' + (data.error || 'Search failed') + '</span>'; return; }
    if (!data.results.length) { box.innerHTML = '<span class="hint">No matches</span>'; return; }
    box.innerHTML = '';
    data.results.forEach(function(r) {
      var div = document.createElement('div');
      div.className = 'search-item';
      div.textContent = r.name;
      div.onclick = function() {
        _ensureLocMap();
        _locMap.setView([r.lat, r.lon], 13);
        _locSetPoint(r.lat, r.lon, false);
        box.innerHTML = '';
      };
      box.appendChild(div);
    });
  } catch(e) {
    box.innerHTML = '<span class="hint">Offline – enter coordinates manually</span>';
  }
}

async function saveSettings() {
  _showDebris = document.getElementById('cfg-show-deb').checked;
  localStorage.setItem('showDebris', _showDebris ? '1' : '0');
  var cfg = {
    lat:         document.getElementById('cfg-lat').value,
    lon:         document.getElementById('cfg-lon').value,
    alt:         document.getElementById('cfg-alt').value,
    min_el:      document.getElementById('cfg-min-el').value,
    alarm_min:   document.getElementById('cfg-alarm-min').value,
    alarm_sound: document.getElementById('cfg-alarm-sound').value,
    tle_source:  document.getElementById('cfg-tle-source').value,
    tle_file:    document.getElementById('cfg-tle-file').value
  };
  await fetch('/api/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(cfg)
  });
  updateObserverMarker(parseFloat(cfg.lat), parseFloat(cfg.lon));
  sendAlarmMinutes(cfg.alarm_min);
  sendAlarmSound(cfg.alarm_sound);
  closeDialog('dlg-settings');
}

// ── Alarm UI ─────────────────────────────────────────────────

var _alarmTimeout = null;

function triggerAlarmUI(data) {
  var banner  = document.getElementById('alarm-banner');
  var text    = document.getElementById('alarm-text');
  var sb      = document.getElementById('statusbar');

  var msg = data.type === 'aos'
    ? '🛰 AOS  ' + data.sat_name + '  –  PASS IN PROGRESS  MAX ' + data.max_el + '°'
    : '⚠ PASS IN ' + data.seconds_to_aos + 's  –  ' + data.sat_name
      + '  AOS: ' + (data.aos_dt || '').slice(11,19) + ' UTC  MAX ' + data.max_el + '°';

  text.textContent = msg;
  banner.classList.remove('hidden');
  banner.classList.add('blink');
  sb.classList.add('alarm');

  // Auto-dismiss warning after 30 seconds, AOS after 60 seconds
  if (_alarmTimeout) clearTimeout(_alarmTimeout);
  _alarmTimeout = setTimeout(dismissAlarm, data.type === 'aos' ? 60000 : 30000);
}

function dismissAlarm() {
  var banner = document.getElementById('alarm-banner');
  var sb     = document.getElementById('statusbar');
  banner.classList.add('hidden');
  banner.classList.remove('blink');
  sb.classList.remove('alarm');
  if (_alarmTimeout) { clearTimeout(_alarmTimeout); _alarmTimeout = null; }
}

// ── Network status ───────────────────────────────────────────

async function checkNetworkStatus() {
  try {
    var res  = await fetch('/api/info');
    var data = await res.json();
    var el   = document.getElementById('status-online');
    if (data.online) {
      el.textContent = 'ONLINE';
      el.className   = 'status-dot online';
    } else {
      el.textContent = 'OFFLINE';
      el.className   = 'status-dot offline';
    }

    var notice = document.getElementById('sb-notice');
    if (notice) {
      var until = (data.celestrak_blocked_until || 0) * 1000;
      if (until > Date.now()) {
        var t = new Date(until);
        notice.textContent = '⚠ Celestrak rate-limited until ' +
          String(t.getHours()).padStart(2, '0') + ':' +
          String(t.getMinutes()).padStart(2, '0') + ' – using cached / local TLEs';
        notice.style.display = '';
      } else {
        notice.style.display = 'none';
      }
    }
  } catch(e) {}
}

// Check network status every 30 seconds
setInterval(checkNetworkStatus, 30000);
checkNetworkStatus();

// ── Keyboard shortcuts ───────────────────────────────────────

document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape') {
    closeAllMenus();
    document.querySelectorAll('.modal-bg.open').forEach(function(d) {
      d.classList.remove('open');
    });
  }
});

// ── Quit ─────────────────────────────────────────────────────

async function quitApp() {
  closeAllMenus();
  if (!confirm('Shut down Kepler73?')) return;
  try {
    await fetch('/api/quit', { method: 'POST' });
  } catch (e) {}
  document.body.innerHTML =
    '<div style="display:flex;height:100vh;align-items:center;justify-content:center;'
    + 'color:var(--fg-dim);font-family:var(--font);font-size:13px">'
    + 'Kepler73 has shut down. You can close this tab.</div>';
}