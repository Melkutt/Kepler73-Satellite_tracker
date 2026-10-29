// Kepler73 - rotor.js
// Az/El rotator (Hamlib rotctld) tracking dialog + live status readout.

var _rotEnabled = false;

// ── Dialog ───────────────────────────────────────────────────

function showRotorDialog() {
  fetch('/api/rotor/config')
    .then(function (r) { return r.json(); })
    .then(function (d) {
      var c = d.config || {};
      document.getElementById('rot-host').value   = c.host || '127.0.0.1';
      document.getElementById('rot-port').value   = c.port || 4533;
      document.getElementById('rot-minel').value  = (c.min_el != null) ? c.min_el : 3;
      document.getElementById('rot-parkaz').value = c.park_az || 0;
      document.getElementById('rot-parkel').value = c.park_el || 0;
      document.getElementById('rot-azdb').value   = (c.az_deadband != null) ? c.az_deadband : 2;
      document.getElementById('rot-eldb').value   = (c.el_deadband != null) ? c.el_deadband : 2;
      _rotEnabled = !!c.enabled;
      _rotReflectToggle();
      document.getElementById('rot-msg').textContent = '';
      openDialog('dlg-rotor');
    })
    .catch(function () { openDialog('dlg-rotor'); });
}

function _rotFormCfg() {
  return {
    host:        (document.getElementById('rot-host').value || '127.0.0.1').trim(),
    port:        parseInt(document.getElementById('rot-port').value, 10) || 4533,
    min_el:      parseFloat(document.getElementById('rot-minel').value) || 0,
    park_az:     parseFloat(document.getElementById('rot-parkaz').value) || 0,
    park_el:     parseFloat(document.getElementById('rot-parkel').value) || 0,
    az_deadband: parseFloat(document.getElementById('rot-azdb').value) || 0,
    el_deadband: parseFloat(document.getElementById('rot-eldb').value) || 0
  };
}

function _rotPost(extra, cb) {
  var body = _rotFormCfg();
  if (extra) Object.keys(extra).forEach(function (k) { body[k] = extra[k]; });
  fetch('/api/rotor/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  })
    .then(function (r) { return r.json(); })
    .then(function (d) { if (cb) cb(d); })
    .catch(function () {
      var m = document.getElementById('rot-msg');
      if (m) { m.textContent = '⚠ could not reach Kepler73'; m.style.color = 'var(--warn)'; }
    });
}

function rotSave() {
  _rotPost({ enabled: _rotEnabled }, function () {
    var m = document.getElementById('rot-msg');
    m.textContent = '💾 Saved'; m.style.color = 'var(--accent)';
  });
}

function rotToggle() {
  _rotEnabled = !_rotEnabled;
  _rotReflectToggle();
  _rotPost({ enabled: _rotEnabled });
}

function _rotReflectToggle() {
  var b = document.getElementById('rot-toggle');
  if (!b) return;
  b.textContent = _rotEnabled ? '⏸ Stop tracking' : '▶ Start tracking';
  b.classList.toggle('rig-on', _rotEnabled);
}

function rotTest() {
  var el = document.getElementById('rot-msg');
  el.textContent = 'Testing…'; el.style.color = 'var(--fg-dim)';
  var f = _rotFormCfg();
  fetch('/api/rotor/test', {
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
          msg += ' — could not open a TCP connection to rotctld.';
        } else {
          var a = (p.attempts || []).map(function (x) {
            return x.cmd + ' → ' + (x.error ? '[' + x.error + ']'
              : JSON.stringify(x.raw || ''));
          }).join('  |  ');
          msg += ' — connected OK. rotctld replied: ' + (a || '(nothing)');
        }
      }
      el.textContent = msg;
    })
    .catch(function () { el.textContent = '⚠ request failed'; el.style.color = 'var(--warn)'; });
}

function rotPark() {
  _rotEnabled = false;
  _rotReflectToggle();
  _rotPost({ enabled: false });          // persist form incl. park position
  fetch('/api/rotor/park', { method: 'POST' })
    .then(function (r) { return r.json(); })
    .then(function (d) {
      var m = document.getElementById('rot-msg');
      m.textContent = d.ok ? '⇤ Parking…' : ('⚠ ' + (d.error || 'park failed'));
      m.style.color = d.ok ? 'var(--accent)' : 'var(--warn)';
    })
    .catch(function () {});
}

function rotStop() {
  _rotEnabled = false;
  _rotReflectToggle();
  fetch('/api/rotor/stop', { method: 'POST' })
    .then(function (r) { return r.json(); })
    .then(function (d) {
      var m = document.getElementById('rot-msg');
      m.textContent = d.ok ? '■ Rotor stopped' : ('⚠ ' + (d.error || 'stop failed'));
      m.style.color = 'var(--warn)';
    })
    .catch(function () {});
}

// ── Live status (from the rotor_status socket event) ───────

function _fmtAzEl(az, el) {
  return az.toFixed(1) + '° / ' + el.toFixed(1) + '°';
}

function updateRotorStatus(s) {
  _rotEnabled = !!s.enabled;

  // Status-bar chip
  var chip = document.getElementById('sb-rot');
  if (chip) {
    if (s.enabled && s.mode === 'tracking') {
      chip.style.display = '';
      chip.textContent = s.connected
        ? '📡 Az ' + Math.round(s.cmd_az) + '° El ' + Math.round(s.cmd_el) + '°'
        : '📡 rotor ⚠';
      chip.style.color = s.connected ? 'var(--accent)' : 'var(--warn)';
    } else if (s.enabled) {
      chip.style.display = '';
      chip.textContent = '📡 ' + (s.mode || 'idle');
      chip.style.color = 'var(--fg-dim)';
    } else {
      chip.style.display = 'none';
    }
  }

  // Dialog readout
  var ro = document.getElementById('rot-ro-sat');
  if (!ro) return;
  ro.textContent = s.sat || '—';
  document.getElementById('rot-ro-mode').textContent = s.mode || '—';
  document.getElementById('rot-ro-cmd').textContent =
    (s.enabled && s.mode !== 'idle') ? _fmtAzEl(s.cmd_az, s.cmd_el) : '—';
  document.getElementById('rot-ro-pos').textContent =
    (s.rot_az || s.rot_el) ? _fmtAzEl(s.rot_az, s.rot_el) : '—';
  var link = document.getElementById('rot-ro-link');
  link.textContent = !s.enabled ? 'off'
    : (s.connected ? 'connected' : (s.error || 'no connection'));
  link.style.color = s.connected ? 'var(--accent)' : (s.enabled ? 'var(--warn)' : 'var(--fg-dim)');
}
