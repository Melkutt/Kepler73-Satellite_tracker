// Kepler73 - transponder.js
// Right slide-in panel: transponders with Doppler, checkboxes, pass info

var _txData      = [];
var _txNorad     = '';
var _txSatName   = '';
var _txSelected  = {};   // uuid -> bool
var _txVRadial   = 0;    // m/s, current radial velocity
var _txPassData  = null; // current pass info {aos_jd, los_jd, max_v_radial}
var C_LIGHT      = 299792458;  // m/s

// ── Open / close right panel ─────────────────────────────────

function openRightPanel(satName, noradId) {
  _txSatName  = satName;
  _txNorad    = noradId;
  _txSelected = {};
  _txPassData = null;
  document.getElementById('rp-sat-name').textContent = satName;
  document.getElementById('right-panel').classList.add('open');
  loadTransponders(satName, noradId, false);
}

function closeRightPanel() {
  document.getElementById('right-panel').classList.remove('open');
  _txNorad    = '';
  _txPassData = null;
}

function refreshTransponders() {
  if (_txNorad) loadTransponders(_txSatName, _txNorad, true);
}

// ── Store pass data for max-doppler prediction ───────────────

function setTxPassData(passData) {
  // passData: { aos_jd, los_jd, max_v_radial }
  _txPassData = passData;
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
  } catch(e) {
    list.innerHTML = '<span class="hint-text">Offline – no data</span>';
  }
}

// ── Render transponder list ──────────────────────────────────

function renderTransponderList() {
  var list = document.getElementById('transponder-list');
  list.innerHTML = '';

  _txData.forEach(function(tx, i) {
    var checked  = !!_txSelected[tx.uuid];
    var inactive = !tx.active;
    var div = document.createElement('div');
    div.className = 'tx-item' + (inactive ? ' tx-inactive' : '') + (checked ? ' tx-checked' : '');
    div.dataset.uuid = tx.uuid;

    div.innerHTML =
      '<input type="checkbox" class="tx-check"' + (checked ? ' checked' : '') + '>'
      + '<div class="tx-status">' + (tx.active ? '●' : '○') + '</div>'
      + '<div class="tx-info">'
      +   '<div class="tx-desc">' + tx.description + '</div>'
      +   '<div class="tx-freq-row">'
      +     (tx.downlink ? '<span class="tx-base-freq">↓ ' + tx.downlink + '</span>' : '')
      +     (tx.mode ? ' <span class="tx-mode">' + tx.mode + '</span>' : '')
      +   '</div>'
      +   (checked ? '<div class="tx-doppler-row" id="dop-' + tx.uuid + '">'
      +     + _dopplerHtml(tx) + '</div>' : '')
      + '</div>'
      + '<button class="tx-info-btn" title="Details">ℹ</button>';

    div.querySelector('.tx-check').addEventListener('change', function(e) {
      e.stopPropagation();
      _txSelected[tx.uuid] = e.target.checked;
      renderTransponderList();
    });
    div.querySelector('.tx-info-btn').addEventListener('click', function(e) {
      e.stopPropagation();
      showTransponderDialog(i);
    });

    list.appendChild(div);
  });
}

// ── Doppler calculation ──────────────────────────────────────

function _dopplerHz(f0_hz, v) {
  return f0_hz * (1 - v / C_LIGHT);
}

function _fmtMhz(hz) {
  return (hz / 1e6).toFixed(4) + ' MHz';
}

function _dopplerHtml(tx) {
  var inPass  = _inPass();
  var v       = inPass ? _txVRadial : (_txPassData ? _txPassData.max_v_radial : _txVRadial);
  var label   = inPass ? 'LIVE' : (_txPassData ? 'MAX Δ at pass' : 'Doppler');
  var color   = inPass ? 'var(--accent)' : 'var(--warn)';
  var html    = '<span style="font-size:9px;color:' + color + ';font-weight:bold">'
    + label + '</span><br>';

  if (tx.downlink_hz) {
    var dop  = _dopplerHz(tx.downlink_hz, v);
    var diff = dop - tx.downlink_hz;
    var sign = diff >= 0 ? '+' : '';
    if (inPass) {
      html += '<span class="dop-label">↓</span> '
        + '<span class="dop-freq">' + _fmtMhz(dop) + '</span>'
        + ' <span class="dop-delta">(' + sign + (diff/1000).toFixed(2) + ' kHz)</span><br>';
    } else {
      html += '<span class="dop-label">↓ shift up to:</span> '
        + '<span class="dop-delta">' + sign + (diff/1000).toFixed(2) + ' kHz</span><br>';
    }
  }
  if (tx.uplink_hz) {
    var dopU  = _dopplerHz(tx.uplink_hz, v);
    var diffU = dopU - tx.uplink_hz;
    var signU = diffU >= 0 ? '+' : '';
    if (inPass) {
      html += '<span class="dop-label">↑</span> '
        + '<span class="dop-freq">' + _fmtMhz(dopU) + '</span>'
        + ' <span class="dop-delta">(' + signU + (diffU/1000).toFixed(2) + ' kHz)</span>';
    } else {
      html += '<span class="dop-label">↑ shift up to:</span> '
        + '<span class="dop-delta">' + signU + (diffU/1000).toFixed(2) + ' kHz</span>';
    }
  }
  if (!tx.downlink_hz && !tx.uplink_hz) {
    html += '<span class="dop-label">No frequency data</span>';
  }
  return html;
}

// ── Update only doppler values without full re-render ────────

function renderDopplerValues() {
  _txData.forEach(function(tx) {
    if (!_txSelected[tx.uuid]) return;
    var el = document.getElementById('dop-' + tx.uuid);
    if (el) el.innerHTML = _dopplerHtml(tx);
  });
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
    + '<tr><td>Mode</td><td>' + tx.mode + '</td></tr>';

  if (tx.downlink) html += '<tr><td>Downlink</td><td>' + tx.downlink + '</td></tr>';
  if (tx.uplink)   html += '<tr><td>Uplink</td><td>'   + tx.uplink   + '</td></tr>';
  if (tx.invert)   html += '<tr><td>Inverting</td><td>Yes</td></tr>';
  if (tx.baud)     html += '<tr><td>Baud rate</td><td>' + tx.baud    + '</td></tr>';
  html += '</table>';
  html += '<div style="margin-top:8px;font-size:11px;color:var(--fg-dim)">'
    + 'Source: <a href="https://db.satnogs.org" target="_blank" '
    + 'style="color:var(--accent2)">SatNOGS DB</a></div>';

  document.getElementById('tx-dlg-content').innerHTML = html;
  openDialog('dlg-transponder');
}

// ── Update pass info in right panel ─────────────────────────

function updateRightPanelPass(passHtml) {
  var el = document.getElementById('rp-pass-info');
  if (el) el.innerHTML = passHtml;
}