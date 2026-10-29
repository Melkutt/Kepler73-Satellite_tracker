// Kepler73 - hamlib.js
// Radio / CAT (Hamlib rigctld) Doppler-tuning dialog + live status readout.

var _rigEnabled = false;
window._rigBands = '';

// ── Band parsing (radio capability) ─────────────────────────
// "144-146, 430-440" -> [[144e6,146e6],[430e6,440e6]]. A bare number becomes
// a +/-0.5 MHz window. Empty string -> [] (meaning "no limit").

function rigBandRanges() {
  var out = [];
  (window._rigBands || '').split(',').forEach(function (part) {
    part = part.trim();
    if (!part) return;
    var m = part.match(/^(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)$/);
    if (m) {
      out.push([parseFloat(m[1]) * 1e6, parseFloat(m[2]) * 1e6]);
    } else {
      var one = parseFloat(part);
      if (isFinite(one)) out.push([one * 1e6 - 5e5, one * 1e6 + 5e5]);
    }
  });
  return out;
}

// ── Dialog ───────────────────────────────────────────────────

function showRadioDialog() {
  fetch('/api/rig/config')
    .then(function (r) { return r.json(); })
    .then(function (d) {
      var c = d.config || {};
      document.getElementById('rig-host').value     = c.host || '127.0.0.1';
      document.getElementById('rig-port').value     = c.port || 4532;
      document.getElementById('rig-down').value     = c.down_hz ? (c.down_hz / 1e6) : '';
      document.getElementById('rig-up').value       = c.up_hz   ? (c.up_hz   / 1e6) : '';
      document.getElementById('rig-deadband').value = c.deadband_hz || 10;
      document.getElementById('rig-bands').value    = c.bands || '';
      document.getElementById('rig-tx').checked     = !!c.tx_enabled;
      window._rigBands = c.bands || '';
      _rigEnabled = !!c.enabled;
      _rigReflectToggle();
      document.getElementById('rig-msg').textContent = '';
      openDialog('dlg-radio');
    })
    .catch(function () { openDialog('dlg-radio'); });
}

function _rigFormCfg() {
  var down = parseFloat(document.getElementById('rig-down').value);
  var up   = parseFloat(document.getElementById('rig-up').value);
  return {
    host:        (document.getElementById('rig-host').value || '127.0.0.1').trim(),
    port:        parseInt(document.getElementById('rig-port').value, 10) || 4532,
    down_hz:     isFinite(down) ? Math.round(down * 1e6) : 0,
    up_hz:       isFinite(up)   ? Math.round(up   * 1e6) : 0,
    deadband_hz: parseFloat(document.getElementById('rig-deadband').value) || 10,
    bands:       document.getElementById('rig-bands').value.trim(),
    tx_enabled:  document.getElementById('rig-tx').checked
  };
}

function _rigPost(extra, cb) {
  var body = _rigFormCfg();
  if (extra) Object.keys(extra).forEach(function (k) { body[k] = extra[k]; });
  fetch('/api/rig/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  })
    .then(function (r) { return r.json(); })
    .then(function (d) { if (cb) cb(d); })
    .catch(function () {
      var m = document.getElementById('rig-msg');
      if (m) { m.textContent = '⚠ could not reach Kepler73'; m.style.color = 'var(--warn)'; }
    });
}

function rigSave() {
  _rigPost({ enabled: _rigEnabled }, function () {
    window._rigBands = document.getElementById('rig-bands').value.trim();
    if (typeof renderTransponderList === 'function' &&
        Array.isArray(window._txData) && window._txData.length) {
      renderTransponderList();
    }
    var m = document.getElementById('rig-msg');
    m.textContent = '💾 Saved'; m.style.color = 'var(--accent)';
  });
}

function rigToggle() {
  _rigEnabled = !_rigEnabled;
  _rigReflectToggle();
  _rigPost({ enabled: _rigEnabled });
}

// Toggle from the small strip in the transponder panel (no dialog open).
function rigToggleFromPanel() {
  _rigEnabled = !_rigEnabled;
  fetch('/api/rig/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled: _rigEnabled })
  }).catch(function () {});
}

function _rigReflectToggle() {
  var b = document.getElementById('rig-toggle');
  if (!b) return;
  b.textContent = _rigEnabled ? '⏸ Stop Doppler' : '▶ Start Doppler';
  b.classList.toggle('rig-on', _rigEnabled);
}

function rigTest() {
  var el = document.getElementById('rig-msg');
  el.textContent = 'Testing…'; el.style.color = 'var(--fg-dim)';
  var f = _rigFormCfg();
  fetch('/api/rig/test', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ host: f.host, port: f.port })
  })
    .then(function (r) { return r.json(); })
    .then(function (d) {
      el.style.color = d.ok ? 'var(--accent)' : 'var(--warn)';
      var msg = (d.ok ? '✅ ' : '⚠ ') + (d.detail || '');
      var p = d.probe;
      if (!d.ok && p) {
        if (!p.connected) {
          msg += ' — could not open a TCP connection to rigctld.';
        } else {
          var a = (p.attempts || []).map(function (x) {
            return x.cmd + ' → ' + (x.error ? '[' + x.error + ']'
              : JSON.stringify(x.raw || ''));
          }).join('  |  ');
          msg += ' — connected OK. rigctld replied: ' + (a || '(nothing)');
        }
      }
      el.textContent = msg;
    })
    .catch(function () { el.textContent = '⚠ request failed'; el.style.color = 'var(--warn)'; });
}

// ── Live status (from the rig_status socket event) ──────────

function _fmtMHz(hz) {
  return hz ? (hz / 1e6).toFixed(5) + ' MHz' : '—';
}

function _fmtPb(hz) {
  if (!hz) return '';
  if (hz >= 1000) {
    var k = hz / 1000;
    return (Number.isInteger(k) ? k : k.toFixed(1)) + ' kHz';
  }
  return hz + ' Hz';
}

function _rigModeStr(s) {
  if (!s.mode) return '';
  var pb = _fmtPb(s.passband_hz);
  return s.mode + (pb ? ' ' + pb : '');
}

function updateRigStatus(s) {
  _rigEnabled = !!s.enabled;
  if (typeof s.bands === 'string' && s.bands !== window._rigBands) {
    window._rigBands = s.bands;
    if (typeof renderTransponderList === 'function' &&
        Array.isArray(window._txData) && window._txData.length) {
      renderTransponderList();
    }
  }

  // Status-bar: tuned RF + mode + passband, just left of the CAT chip
  var rf = document.getElementById('sb-rf');
  if (rf) {
    var hz = s.rx_cmd_hz || s.rig_hz;
    if (s.enabled && s.connected && hz) {
      rf.textContent = (hz / 1e6).toFixed(5) + ' MHz'
        + (_rigModeStr(s) ? '  ' + _rigModeStr(s) : '');
      rf.style.display = '';
    } else {
      rf.style.display = 'none';
    }
  }

  // Status-bar chip
  var chip = document.getElementById('sb-rig');
  if (chip) {
    if (s.enabled) {
      chip.style.display = '';
      if (s.connected && s.rx_cmd_hz && s.down_hz) {
        var dkhz = (s.rx_cmd_hz - s.down_hz) / 1000;
        chip.textContent = '📻 CAT ' + (dkhz >= 0 ? '+' : '') + dkhz.toFixed(2) + ' kHz';
        chip.style.color = 'var(--accent)';
      } else if (s.connected) {
        chip.textContent = '📻 CAT ready';
        chip.style.color = 'var(--fg-dim)';
      } else {
        chip.textContent = '📻 CAT ⚠';
        chip.style.color = 'var(--warn)';
      }
    } else {
      chip.style.display = 'none';
    }
  }

  // CAT strip in the transponder panel
  var strip = document.getElementById('tx-cat-strip');
  if (strip) {
    if (s.down_hz > 0) {
      strip.style.display = '';
      document.getElementById('tx-cat-freq').textContent = (s.down_hz / 1e6).toFixed(4) + ' MHz';
      var d = document.getElementById('tx-cat-delta');
      if (s.enabled && s.connected && s.rx_cmd_hz) {
        var k = (s.rx_cmd_hz - s.down_hz) / 1000;
        d.textContent = (k >= 0 ? '+' : '') + k.toFixed(2) + ' kHz';
        d.style.color = 'var(--accent)';
      } else if (s.enabled && !s.connected) {
        d.textContent = '⚠ no rig'; d.style.color = 'var(--warn)';
      } else {
        d.textContent = s.enabled ? 'on' : 'off'; d.style.color = 'var(--fg-dim)';
      }
      var tb = document.getElementById('tx-cat-toggle');
      tb.textContent = s.enabled ? '⏸ Doppler' : '▶ Doppler';
      tb.classList.toggle('rig-on', s.enabled);
    } else {
      strip.style.display = 'none';
    }
  }

  // Dialog readout
  var roSat = document.getElementById('rig-ro-sat');
  if (!roSat) return;
  roSat.textContent = s.sat || '—';
  document.getElementById('rig-ro-vr').textContent =
    (typeof s.v_radial === 'number') ? (s.v_radial > 0 ? '+' : '') + s.v_radial + ' m/s' : '—';
  document.getElementById('rig-ro-rx').textContent  = _fmtMHz(s.rx_cmd_hz);
  document.getElementById('rig-ro-rig').textContent = _fmtMHz(s.rig_hz);
  var rm = document.getElementById('rig-ro-mode');
  if (rm) rm.textContent = _rigModeStr(s) || '—';
  var link = document.getElementById('rig-ro-link');
  link.textContent = !s.enabled ? 'off'
    : (s.connected ? 'connected' : (s.error || 'no connection'));
  link.style.color = s.connected ? 'var(--accent)' : (s.enabled ? 'var(--warn)' : 'var(--fg-dim)');
}
