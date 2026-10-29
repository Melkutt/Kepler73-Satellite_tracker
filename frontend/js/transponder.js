// Kepler73 - transponder.js
// Right slide-in panel: transponders with Doppler, single-select, pass info.
//
// One transponder can be selected at a time. The selected one drives the
// Doppler read-out AND is pushed to the CAT engine as the rig's downlink /
// uplink (see hamlib.js). Transponders outside the radio's configured bands
// are greyed out and cannot be selected.

var _txData      = [];
var _txNorad     = '';
var _txSatName   = '';
var _txSelUuid   = null;   // uuid of the selected transponder, or null
var _txVRadial   = 0;      // m/s, current radial velocity
var _txPassData  = null;   // current pass info {aos_jd, los_jd, max_v_radial}
var C_LIGHT      = 299792458;  // m/s

// ── Pin: keep the right panel docked so the map stays usable ─

// Escape text that came from a user / an imported file before it goes into innerHTML.
function _txEsc(v) {
  return String(v == null ? '' : v)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

var _rpPinned = false;
try { _rpPinned = localStorage.getItem('rp_pinned') === '1'; } catch (e) {}

function _persistPin() {
  try { localStorage.setItem('rp_pinned', _rpPinned ? '1' : '0'); } catch (e) {}
}

function _invalidateMap() {
  // Let the CSS margin transition finish, then tell Leaflet its size changed.
  setTimeout(function () {
    if (window.map && typeof window.map.invalidateSize === 'function') {
      window.map.invalidateSize();
    }
  }, 280);
}

function _applyPinState() {
  var rp = document.getElementById('right-panel');
  if (!rp) return;
  rp.classList.toggle('pinned', _rpPinned);
  document.body.classList.toggle('rp-pinned', _rpPinned);
  var btn = document.getElementById('rp-pin');
  if (btn) {
    btn.classList.toggle('active', _rpPinned);
    btn.title = _rpPinned ? 'Unpin panel (back to overlay)'
                          : 'Pin panel (keep open, dock beside the map)';
  }
  _invalidateMap();
}

function togglePinPanel() {
  _rpPinned = !_rpPinned;
  _persistPin();
  if (_rpPinned) document.getElementById('right-panel').classList.add('open');
  _applyPinState();
}

// Restore pin state on load (dock the panel even before a satellite is picked).
_applyPinState();
if (_rpPinned) {
  var _rp0 = document.getElementById('right-panel');
  if (_rp0) _rp0.classList.add('open');
}

// ── Open / close right panel ─────────────────────────────────

function openRightPanel(satName, noradId) {
  _txSatName  = satName;
  _txNorad    = noradId;
  _txSelUuid  = null;
  _txPassData = null;
  document.getElementById('rp-sat-name').textContent = satName;
  document.getElementById('right-panel').classList.add('open');
  if (typeof refreshNoteBadges === 'function') refreshNoteBadges();
  loadTransponders(satName, noradId, false);
}

function closeRightPanel() {
  document.getElementById('right-panel').classList.remove('open');
  if (_rpPinned) {           // closing a docked panel also releases the map space
    _rpPinned = false;
    _persistPin();
    _applyPinState();
  }
  _txNorad    = '';
  _txPassData = null;
}

function refreshTransponders() {
  if (_txNorad) loadTransponders(_txSatName, _txNorad, true);
}

// ── Store pass data for max-doppler prediction ───────────────

function setTxPassData(passData) {
  _txPassData = passData;   // { aos_jd, los_jd, max_v_radial }
}

// ── Receive radial velocity from websocket ───────────────────

function updateDoppler(positions) {
  if (!_txNorad) return;
  var sat = positions[_txSatName];
  if (!sat) return;
  _txVRadial = sat.v_radial || 0;
  renderDopplerValues();
}

// ── Is satellite currently in pass? ─────────────────────────

function _inPass() {
  if (!_txPassData) return false;
  var nowJd = (Date.now() / 86400000) + 2440587.5;
  return nowJd >= _txPassData.aos_jd && nowJd <= _txPassData.los_jd;
}

// ── Load transponders ────────────────────────────────────────

async function loadTransponders(satName, noradId, force) {
  var list = document.getElementById('transponder-list');
  list.innerHTML = '<span class="hint-text">Loading...</span>';

  try {
    var url = '/api/transmitters/' + noradId + (force ? '?refresh=1' : '');
    var res = await fetch(url);
    _txData = await res.json();

    if (!Array.isArray(_txData) || _txData.length === 0) {
      list.innerHTML = '<span class="hint-text">No transponders found</span>';
      return;
    }
    renderTransponderList();
  } catch (e) {
    list.innerHTML = '<span class="hint-text">Offline – no data</span>';
  }
}

// ── Band check (radio capability) ───────────────────────────

function _txOutOfBand(tx) {
  if (typeof rigBandRanges !== 'function') return false;
  var bands = rigBandRanges();
  if (!bands.length) return false;                 // no limit configured
  var hz = tx.downlink_hz || 0;
  if (!hz) return false;
  return !bands.some(function (b) { return hz >= b[0] && hz <= b[1]; });
}

// ── Render transponder list ──────────────────────────────────

function renderTransponderList() {
  var list = document.getElementById('transponder-list');
  list.innerHTML = '';

  _txData.forEach(function (tx, i) {
    var isSel    = (_txSelUuid === tx.uuid);
    var inactive = !tx.active;
    var oob      = _txOutOfBand(tx);

    var div = document.createElement('div');
    div.className = 'tx-item'
      + (inactive ? ' tx-inactive' : '')
      + (isSel ? ' tx-checked' : '')
      + (oob ? ' tx-oob' : '')
      + (tx.manual ? ' tx-manual' : '');
    div.dataset.uuid = tx.uuid;

    div.innerHTML =
      '<input type="checkbox" class="tx-check"' + (isSel ? ' checked' : '')
        + (oob ? ' disabled title="Outside your radio bands"' : '') + '>'
      + '<div class="tx-status">' + (tx.active ? '●' : '○') + '</div>'
      + '<div class="tx-info">'
      +   '<div class="tx-desc" title="' + _txEsc(tx.description) + '">' + _txEsc(tx.description) + '</div>'
      +   '<div class="tx-freq-row">'
      +     (tx.downlink ? '<span class="tx-base-freq">↓ ' + _txEsc(tx.downlink) + '</span>' : '')
      +     (!tx.downlink && tx.uplink ? '<span class="tx-base-freq">↑ ' + _txEsc(tx.uplink) + '</span>' : '')
      +     (tx.mode ? ' <span class="tx-mode">' + _txEsc(tx.mode) + '</span>' : '')
      +     (tx.manual ? ' <span class="tx-manual-tag" title="Added by you – not verified by SatNOGS">✎ manual</span>' : '')
      +     (oob ? ' <span class="tx-oob-tag">out of band</span>' : '')
      +   '</div>'
      +   (isSel ? '<div class="tx-doppler-row" id="dop-' + tx.uuid + '">'
      +     _dopplerHtml(tx) + '</div>' : '')
      + '</div>'
      + '<button class="tx-info-btn" title="Details">ℹ</button>'
      + (tx.manual ? '<button class="tx-edit-btn" title="Edit your transponder">✎</button>' : '');

    var cb = div.querySelector('.tx-check');
    cb.addEventListener('change', function (e) {
      e.stopPropagation();
      _txSelUuid = e.target.checked ? tx.uuid : null;
      _rigUseTransponder(e.target.checked ? tx : null);
      renderTransponderList();
    });
    div.querySelector('.tx-info-btn').addEventListener('click', function (e) {
      e.stopPropagation();
      showTransponderDialog(i);
    });
    var editBtn = div.querySelector('.tx-edit-btn');
    if (editBtn) {
      editBtn.addEventListener('click', function (e) {
        e.stopPropagation();
        txEditOpen(tx);
      });
    }

    list.appendChild(div);
  });
}

// ── Push the selected transponder to the CAT engine ─────────

function _rigUseTransponder(tx) {
  var body = {
    down_hz: (tx && tx.downlink_hz) ? Math.round(tx.downlink_hz) : 0,
    up_hz:   (tx && tx.uplink_hz)   ? Math.round(tx.uplink_hz)   : 0
  };
  fetch('/api/rig/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  }).catch(function () {});
}

// ── Doppler calculation ──────────────────────────────────────

function _dopplerHz(f0_hz, v) {
  return f0_hz * (1 - v / C_LIGHT);
}

function _fmtMhz(hz) {
  return (hz / 1e6).toFixed(4) + ' MHz';
}

function _dopplerHtml(tx) {
  var inPass = _inPass();
  var v      = inPass ? _txVRadial : (_txPassData ? _txPassData.max_v_radial : _txVRadial);
  var label  = inPass ? 'LIVE' : (_txPassData ? 'MAX Δ at pass' : 'Doppler');
  var color  = inPass ? 'var(--accent)' : 'var(--warn)';
  var html   = '<span style="font-size:9px;color:' + color + ';font-weight:bold">'
    + label + '</span><br>';

  if (tx.downlink_hz) {
    var dop  = _dopplerHz(tx.downlink_hz, v);
    var diff = dop - tx.downlink_hz;
    var sign = diff >= 0 ? '+' : '';
    if (inPass) {
      html += '<span class="dop-label">↓</span> '
        + '<span class="dop-freq">' + _fmtMhz(dop) + '</span>'
        + ' <span class="dop-delta">(' + sign + (diff / 1000).toFixed(2) + ' kHz)</span><br>';
    } else {
      html += '<span class="dop-label">↓ shift up to:</span> '
        + '<span class="dop-delta">' + sign + (diff / 1000).toFixed(2) + ' kHz</span><br>';
    }
  }
  if (tx.uplink_hz) {
    var dopU  = _dopplerHz(tx.uplink_hz, v);
    var diffU = dopU - tx.uplink_hz;
    var signU = diffU >= 0 ? '+' : '';
    if (inPass) {
      html += '<span class="dop-label">↑</span> '
        + '<span class="dop-freq">' + _fmtMhz(dopU) + '</span>'
        + ' <span class="dop-delta">(' + signU + (diffU / 1000).toFixed(2) + ' kHz)</span>';
    } else {
      html += '<span class="dop-label">↑ shift up to:</span> '
        + '<span class="dop-delta">' + signU + (diffU / 1000).toFixed(2) + ' kHz</span>';
    }
  }
  if (!tx.downlink_hz && !tx.uplink_hz) {
    html += '<span class="dop-label">No frequency data</span>';
  }
  return html;
}

// ── Update only doppler values without full re-render ────────

function renderDopplerValues() {
  if (!_txSelUuid) return;
  var el = document.getElementById('dop-' + _txSelUuid);
  var tx = _txData.find(function (t) { return t.uuid === _txSelUuid; });
  if (el && tx) el.innerHTML = _dopplerHtml(tx);
}

// ── Transponder detail dialog ────────────────────────────────

function showTransponderDialog(idx) {
  var tx = _txData[idx];
  if (!tx) return;

  document.getElementById('tx-dlg-title').textContent =
    _txSatName + ' – ' + tx.description;

  var statusColor = tx.active ? '#00e676' : '#ef5350';
  var html = '<table class="tx-table">'
    + '<tr><td>Status</td><td style="color:' + statusColor + '">'
    + (tx.active ? 'ACTIVE' : 'INACTIVE') + '</td></tr>'
    + '<tr><td>Mode</td><td>' + _txEsc(tx.mode) + '</td></tr>';

  if (tx.downlink) html += '<tr><td>Downlink</td><td>' + _txEsc(tx.downlink) + '</td></tr>';
  if (tx.uplink)   html += '<tr><td>Uplink</td><td>'   + _txEsc(tx.uplink)   + '</td></tr>';
  if (tx.invert)   html += '<tr><td>Inverting</td><td>Yes</td></tr>';
  if (tx.baud)     html += '<tr><td>Baud rate</td><td>' + tx.baud    + '</td></tr>';
  if (tx.manual && tx.note) html += '<tr><td>Note</td><td>' + _txEsc(tx.note) + '</td></tr>';
  html += '</table>';
  if (tx.manual) {
    html += '<div style="margin-top:8px;font-size:11px;color:var(--warn)">'
      + '✎ Added manually by you – not verified by SatNOGS, and kept when the '
      + 'SatNOGS list is refreshed.</div>';
  } else {
    html += '<div style="margin-top:8px;font-size:11px;color:var(--fg-dim)">'
      + 'Source: <a href="https://db.satnogs.org" target="_blank" '
      + 'style="color:var(--accent2)">SatNOGS DB</a></div>';
  }

  document.getElementById('tx-dlg-content').innerHTML = html;
  openDialog('dlg-transponder');
}

// ── Update pass info in right panel ─────────────────────────

function updateRightPanelPass(passHtml) {
  var el = document.getElementById('rp-pass-info');
  if (el) el.innerHTML = passHtml;
}
