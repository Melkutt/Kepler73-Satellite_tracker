// Kepler73 - sidebar.js
// Satellite list, module name and pass info in the side panel

var _selectedSat       = null;
var _activeModule      = null;
var _searchResults     = [];
var _selectedSearchIdx = -1;
var _alarmSats         = {};   // norad_id -> bool (true = alarm enabled)
var _followSat         = null; // name of the satellite the map is locked onto

// ── Module ───────────────────────────────────────────────────

async function loadModuleList() {
  var res  = await fetch('/api/modules');
  var data = await res.json();
  _activeModule = data.active;

  document.getElementById('module-name').textContent =
    data.active ? data.active.toUpperCase() : '—';
  document.getElementById('sb-module').textContent =
    data.active ? 'MODULE: ' + data.active : 'No module';

  // Build module menu
  var menuDiv = document.getElementById('module-list-menu');
  menuDiv.innerHTML = '';
  data.modules.forEach(function(name) {
    var a = document.createElement('a');
    a.textContent = (name === data.active ? '▶ ' : '   ') + name;
    a.onclick = function() { activateModule(name); };
    menuDiv.appendChild(a);
  });
}

var _modGen = 0;   // bumped on every module switch to cancel stale reloads

async function activateModule(name) {
  var gen = ++_modGen;
  closeAllMenus();
  await fetch('/api/modules/' + encodeURIComponent(name) + '/activate',
    { method: 'POST' });
  if (gen !== _modGen) return;
  _followSat = null;
  if (typeof setFollowSat === 'function') setFollowSat(null);
  await loadModuleList();
  if (gen !== _modGen) return;
  await loadSatList();
}

// ── Satellite list ───────────────────────────────────────────

async function loadSatList() {
  var modName = _activeModule;
  if (!modName) return;
  var gen = _modGen;

  var mod = null;
  try {
    var res = await fetch('/api/modules/' + encodeURIComponent(modName));
    mod = await res.json();
  } catch (e) { mod = null; }

  // A newer module switch happened while we were fetching – let it win.
  if (_activeModule !== modName || gen !== _modGen) return;

  var list = document.getElementById('sat-list');
  list.innerHTML = '';   // never leave the previous module's satellites showing

  if (!mod || !mod.satellites) {
    list.innerHTML = '<span class="hint-text">Could not load satellites – retry</span>';
    return;
  }

  var sats = mod.satellites || [];
  sats.forEach(function(sat) {
    // Read alarm state from module JSON on first load, then use local state
    if (_alarmSats[sat.norad_id] === undefined) {
      _alarmSats[sat.norad_id] = sat.alarm_enabled === true;
    }
    var alarmed = _alarmSats[sat.norad_id] === true;

    var div = document.createElement('div');
    div.className    = 'sat-item' + (_selectedSat === sat.name ? ' selected' : '');
    div.dataset.name  = sat.name;
    div.dataset.norad = sat.norad_id;
    var followed = (_followSat === sat.name);
    div.innerHTML = '<input type="checkbox" class="alarm-check" title="Enable alarm"'
      + (alarmed ? ' checked' : '') + '>'
      + '<div class="sat-dot" style="background:' + (sat.color || '#00e676') + '"></div>'
      + '<div class="sat-info">'
      + '<div class="sat-name">' + sat.name + '</div>'
      + '<div class="sat-sub">#' + sat.norad_id + ' &nbsp;|&nbsp; <span id="cd-' + sat.norad_id + '">--:--</span></div>'
      + '</div>'
      + '<div class="sat-el" id="el-' + sat.norad_id + '">—°</div>'
      + '<input type="checkbox" class="follow-check" title="Lock map on this satellite"'
      + (followed ? ' checked' : '') + '>';

    // Fetch countdown immediately for this satellite
    _fetchCountdown(sat.norad_id);

    // Flag decayed satellites (grey out, no countdown/pass)
    _checkSatcat(sat.norad_id, div);

    // Checkbox toggles alarm without selecting satellite
    div.querySelector('.alarm-check').addEventListener('change', function(e) {
      e.stopPropagation();
      _alarmSats[sat.norad_id] = e.target.checked;
      notifyAlarmSats();
      // Persist to module JSON
      fetch('/api/modules/' + encodeURIComponent(_activeModule) + '/satellites/' + sat.norad_id + '/alarm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ alarm_enabled: e.target.checked })
      }).catch(function() {});
    });

    // Follow checkbox: lock the map onto this satellite (only one at a time)
    div.querySelector('.follow-check').addEventListener('change', function(e) {
      e.stopPropagation();
      if (e.target.checked) {
        _followSat = sat.name;
        document.querySelectorAll('.follow-check').forEach(function(cb) {
          if (cb !== e.target) cb.checked = false;
        });
      } else if (_followSat === sat.name) {
        _followSat = null;
      }
      if (typeof setFollowSat === 'function') setFollowSat(_followSat);
    });

    div.onclick = function(e) {
      if (e.target.classList.contains('alarm-check')) return;
      if (e.target.classList.contains('follow-check')) return;
      selectSat(sat.name);
    };
    list.appendChild(div);
  });

  document.getElementById('sb-sats').textContent =
    sats.length + ' satellite' + (sats.length !== 1 ? 's' : '');

  // Tell server which satellites have alarms enabled (default: none)
  notifyAlarmSats();
}

function selectSat(name) {
  _selectedSat = name;
  document.querySelectorAll('.sat-item').forEach(function(el) {
    el.classList.toggle('selected', el.dataset.name === name);
  });
  document.getElementById('sb-selected').textContent = name ? '▶ ' + name : '';

  notifySelected(name);

  // Deactivate simulator when satellite changes
  if (typeof simOnSatChange === 'function') simOnSatChange();

  if (name) {
    var item = document.querySelector('.sat-item[data-name="' + name + '"]');
    if (item) {
      openRightPanel(name, item.dataset.norad);
      loadNextPass(name);
    }
  } else {
    closeRightPanel();
  }
}

function getSelectedSat() { return _selectedSat; }

// ── Live position update in sidebar ─────────────────────────

function updateSidebarPositions(data) {
  Object.entries(data).forEach(function(entry) {
    var s  = entry[1];
    var el = document.getElementById('el-' + s.norad_id);
    if (el) {
      el.textContent = s.el.toFixed(0) + '°';
      el.className   = 'sat-el' + (s.el > 0 ? ' above' : '');
    }
  });
}

// ── Next pass ────────────────────────────────────────────────

var _lastPassData = null;  // Store for save function

async function loadNextPass(satName) {
  var item = document.querySelector('.sat-item[data-name="' + satName + '"]');
  if (!item) return;
  var norad = item.dataset.norad;
  var dot   = item.querySelector('.sat-dot');
  var color = dot ? dot.style.background : '#00e676';

  var div = document.getElementById('rp-pass-info');
  if (div) div.textContent = 'Calculating...';

  try {
    var res = await fetch('/api/passes/' + norad);
    if (!res.ok) {
      if (div) div.textContent = 'No pass within 7 days.';
      clearPolarMap();
      return;
    }
    var p = await res.json();

    // Store pass data for save function
    _lastPassData = { sat: satName, norad: norad, pass: p };

    var passHtml = '<b style="color:var(--accent)">' + satName + '</b><br>'
      + 'AOS: ' + p.aos_dt + ' UTC<br>'
      + 'MAX: ' + p.max_el + '°  @ ' + p.max_el_dt.slice(11,19) + '<br>'
      + 'LOS: ' + p.los_dt.slice(11,19) + ' UTC<br>'
      + 'Dir: ' + p.aos_compass + ' \u2192 ' + p.los_compass + '<br>'
      + 'Dur: ' + Math.floor(p.duration/60) + 'm ' + (p.duration%60) + 's'
      + '<br><button onclick="savePassToFile()" style="margin-top:6px;font-size:10px;'
      + 'background:var(--select);color:var(--fg);border:1px solid var(--accent);'
      + 'padding:2px 8px;cursor:pointer;border-radius:2px;">💾 Save pass</button>';

    if (div) div.innerHTML = passHtml;

    setTxPassData({
      aos_jd:       p.aos_jd,
      los_jd:       p.los_jd,
      max_v_radial: p.max_v_radial || 0
    });

    updatePolarMap(satName, color, p);

  } catch(e) {
    if (div) div.textContent = 'Error calculating pass.';
    clearPolarMap();
  }
}

async function savePassToFile() {
  if (!_lastPassData) return;
  var sat  = _lastPassData.sat;
  var norad = _lastPassData.norad;
  var p    = _lastPassData.pass;

  // Fetch transponders for this satellite
  var txLines = '';
  try {
    var txRes = await fetch('/api/transmitters/' + norad);
    var txData = await txRes.json();
    if (txData && txData.length) {
      txLines += '\n--- TRANSPONDERS ---\n';
      txData.forEach(function(tx) {
        if (!tx.active) return;
        txLines += tx.description + '\n';
        txLines += '  Mode:     ' + (tx.mode || '?') + '\n';
        if (tx.downlink_hz) txLines += '  Downlink: ' + (tx.downlink_hz / 1e6).toFixed(4) + ' MHz\n';
        if (tx.uplink_hz)   txLines += '  Uplink:   ' + (tx.uplink_hz   / 1e6).toFixed(4) + ' MHz\n';
        if (tx.baud)        txLines += '  Baud:     ' + tx.baud + '\n';
        txLines += '\n';
      });
    }
  } catch(e) {}

  // Build text content
  var lines = [
    '========================================',
    '  KEPLER73 - PASS LOG',
    '  Generated: ' + new Date().toUTCString(),
    '========================================',
    '',
    '--- SATELLITE ---',
    'Name:     ' + sat,
    'NORAD ID: ' + norad,
    '',
    '--- PASS DATA ---',
    'AOS:      ' + p.aos_dt + ' UTC  (' + p.aos_az + '°  ' + p.aos_compass + ')',
    'MAX el:   ' + p.max_el + '°  at  ' + p.max_el_dt + ' UTC  (' + p.max_az + '°)',
    'LOS:      ' + p.los_dt + ' UTC  (' + p.los_az + '°  ' + p.los_compass + ')',
    'Duration: ' + Math.floor(p.duration/60) + 'm ' + (p.duration%60) + 's',
    'Max Doppler shift: ±' + (p.max_v_radial ? (p.max_v_radial / 1000).toFixed(2) : '?') + ' km/s',
    '',
    '--- PASS TRACK ---',
    ' Time (UTC)            Az       El',
    '----------------------------------------------',
  ];

  // Generate ~25 track points evenly spaced through the pass
  if (p.track && p.track.length > 1) {
    var track  = p.track;
    var nSteps = Math.min(25, track.length);
    var step   = (track.length - 1) / (nSteps - 1);

    // AOS time in ms
    var aosMsec = Date.parse(p.aos_dt.replace(' ', 'T') + 'Z');
    var losMsec = Date.parse(p.los_dt.replace(' ', 'T') + 'Z');
    var durMsec = losMsec - aosMsec;

    for (var i = 0; i < nSteps; i++) {
      var idx = Math.round(i * step);
      var pt  = track[Math.min(idx, track.length - 1)];
      var frac = i / (nSteps - 1);
      var tMsec = aosMsec + frac * durMsec;
      var dt  = new Date(tMsec);

      var hh  = String(dt.getUTCHours()).padStart(2,'0');
      var mm  = String(dt.getUTCMinutes()).padStart(2,'0');
      var ss  = String(dt.getUTCSeconds()).padStart(2,'0');
      var dd  = dt.getUTCFullYear() + '/' +
                String(dt.getUTCMonth()+1).padStart(2,'0') + '/' +
                String(dt.getUTCDate()).padStart(2,'0');

      var azStr = pt[0].toFixed(2).padStart(7);
      var elStr = pt[1].toFixed(2).padStart(7);
      lines.push(' ' + dd + ' ' + hh + ':' + mm + ':' + ss + '  ' + azStr + '  ' + elStr);
    }
  }

  lines.push('----------------------------------------------');
  lines.push(txLines);
  lines.push('========================================');

  var text = lines.join('\n');

  // Build filename: NOAA19_20260404_2143.txt
  var aos = p.aos_dt.replace(/[^0-9]/g, '');
  var fname = sat.replace(/\s+/g, '') + '_' + aos.slice(0,8) + '_' + aos.slice(8,12) + '.txt';

  // Trigger download
  var blob = new Blob([text], { type: 'text/plain' });
  var a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = fname;
  a.click();
  URL.revokeObjectURL(a.href);
}

// ── Alarm satellite selection ────────────────────────────────

function notifyAlarmSats() {
  var enabled = Object.keys(_alarmSats).filter(function(id) {
    return _alarmSats[id] !== false;
  });
  socket.emit('set_alarm_sats', { norad_ids: enabled });
}

// ── Decayed satellites ───────────────────────────────────────

var _decayed = {};  // norad_id -> true

function _checkSatcat(noradId, itemEl) {
  fetch('/api/satcat/' + noradId)
    .then(function(r) { return r.json(); })
    .then(function(s) {
      if (!s || !s.decayed) return;
      _decayed[noradId] = true;
      delete _countdowns[noradId];
      itemEl.classList.add('decayed');
      var sub = itemEl.querySelector('.sat-sub');
      if (sub) {
        sub.innerHTML = '#' + noradId +
          ' &nbsp;|&nbsp; <span class="decay-tag">↓ re-entered ' +
          (s.decay_date || '') + '</span>';
      }
      var cd = document.getElementById('cd-' + noradId);
      if (cd) cd.textContent = '';
      var el = document.getElementById('el-' + noradId);
      if (el) { el.textContent = '—'; el.className = 'sat-el'; }
    })
    .catch(function() {});
}

// ── Countdown timers ─────────────────────────────────────────

var _countdowns = {};  // norad_id -> aos_utc_ms (JS timestamp)

function _fetchCountdown(noradId) {
  if (_decayed[noradId]) return;
  fetch('/api/passes/' + noradId)
    .then(function(r) { return r.json(); })
    .then(function(p) {
      if (_decayed[noradId]) return;
      if (p && p.aos_dt) {
        // aos_dt is "YYYY-MM-DD HH:MM:SS" UTC
        var ms = Date.parse(p.aos_dt.replace(' ', 'T') + 'Z');
        _countdowns[noradId] = ms;
      }
    })
    .catch(function() {});
}

function _formatCountdown(ms) {
  var secs = Math.floor(ms / 1000);
  if (secs < 0) secs = 0;
  var h = Math.floor(secs / 3600);
  var m = Math.floor((secs % 3600) / 60);
  if (h > 99) return '--:--';
  return String(h).padStart(2, '0') + ':' + String(m).padStart(2, '0');
}

function _tickCountdowns() {
  var now = Date.now();
  Object.keys(_countdowns).forEach(function(noradId) {
    var el = document.getElementById('cd-' + noradId);
    if (!el) return;
    var diff = _countdowns[noradId] - now;
    if (diff < 0) {
      // Pass may have started or ended – refetch
      el.textContent = '--:--';
      delete _countdowns[noradId];
      _fetchCountdown(noradId);
      return;
    }
    el.textContent = _formatCountdown(diff);
  });
}

function _initCountdowns() {
  // Fetch countdown for all satellites currently in the list
  document.querySelectorAll('.sat-item').forEach(function(item) {
    _fetchCountdown(item.dataset.norad);
  });
  setInterval(_tickCountdowns, 1000);
  // Refresh AOS data every 5 minutes
  setInterval(function() {
    document.querySelectorAll('.sat-item').forEach(function(item) {
      _fetchCountdown(item.dataset.norad);
    });
  }, 300000);
}

// ── Init ─────────────────────────────────────────────────────

(async function() {
  await loadModuleList();
  await loadSatList();
  _initCountdowns();
})();