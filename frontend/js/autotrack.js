// Kepler73 - autotrack.js
// Auto-track dialog (pick satellites + thresholds + commands), the upcoming-pass list and
// the status-bar chip. The planning and the switching itself happen on the server
// (backend/autotrack.py); this only edits the settings and mirrors what the server does.

var _atSats  = [];      // [{norad, name, modules:[...]}] over every module
var _atSel   = {};      // norad -> {on, min_el, down_mhz, module}
var _atChip  = null;    // last auto_status from the server
var _atPoll  = 0;       // generation counter for the "wait for the plan" polling

function _atEl(id) { return document.getElementById(id); }

// ── Dialog ───────────────────────────────────────────────────

function showAutoDialog() {
  closeAllMenus();
  openDialog('dlg-auto');
  _atEl('auto-msg').textContent = '';
  _atLoad();
}

// Age in days of a TLE, from the epoch in line 1 (YYDDD.DDDDDDDD).
function _atTleAge(l1) {
  if (!l1 || l1.length < 32) return null;
  var yy = parseInt(l1.substr(18, 2), 10), day = parseFloat(l1.substr(20, 12));
  if (isNaN(yy) || isNaN(day)) return null;
  var ms = Date.UTC(yy < 57 ? 2000 + yy : 1900 + yy, 0, 1) + (day - 1) * 86400000;
  return (Date.now() - ms) / 86400000;
}

function _atAgeText(d) {
  if (d == null) return '?';
  if (d < 1) return '< 1 d';
  return d >= 730 ? (d / 365.25).toFixed(1) + ' y' : Math.round(d) + ' d';
}

function _atStale(s) {
  var lim = parseInt(_atEl('auto-maxage').value, 10) || 0;
  return lim > 0 && s && s.age != null && s.age > lim;
}

async function _atLoadSats() {
  var out = {}, mods = [];
  try {
    var m = await (await fetch('/api/modules')).json();
    mods = m.modules || [];
    for (var i = 0; i < mods.length; i++) {
      var mod = await (await fetch('/api/modules/' + encodeURIComponent(mods[i]))).json();
      (mod.satellites || []).forEach(function (s) {
        var o = out[s.norad_id] || (out[s.norad_id] = { norad: s.norad_id, name: s.name, modules: [], age: null });
        if (o.modules.indexOf(mods[i]) < 0) o.modules.push(mods[i]);
        var a = _atTleAge(s.tle_line1);
        if (a != null && (o.age == null || a < o.age)) o.age = a;       // newest element set wins
      });
    }
  } catch (e) { /* leave whatever we got */ }
  _atSats = Object.keys(out).map(function (k) { return out[k]; });
  _atSats.sort(function (a, b) {
    return (a.modules[0] + '\u0000' + a.name).localeCompare(b.modules[0] + '\u0000' + b.name);
  });

  var sel = _atEl('auto-modfilter'), keep = sel.value;
  sel.innerHTML = '';
  var all = document.createElement('option'); all.value = ''; all.textContent = 'All modules';
  sel.appendChild(all);
  mods.forEach(function (n) {
    var o = document.createElement('option'); o.value = n; o.textContent = n; sel.appendChild(o);
  });
  sel.value = keep;
}

async function _atLoad() {
  var snap = null;
  try { snap = await (await fetch('/api/autotrack')).json(); } catch (e) { snap = null; }
  await _atLoadSats();
  if (!snap || !snap.config) { _atEl('auto-msg').textContent = '⚠ could not reach Kepler73'; return; }

  var c = snap.config;
  _atEl('auto-enabled').checked  = !!c.enabled;
  _atEl('auto-minel').value      = c.default_min_el;
  _atEl('auto-lead').value       = c.lead_sec;
  _atEl('auto-tail').value       = c.tail_sec;
  _atEl('auto-rotor').checked    = !!c.use_rotor;
  _atEl('auto-cat').checked      = !!c.use_cat;
  _atEl('auto-maxage').value     = c.max_tle_age_days;
  _atEl('auto-cmd-start').value  = c.cmd_start || '';
  _atEl('auto-cmd-end').value    = c.cmd_end || '';

  _atSel = {};
  (c.entries || []).forEach(function (e) {
    _atSel[e.norad_id] = { on: !!e.enabled, min_el: e.min_el, down_mhz: e.down_mhz, module: e.module };
  });
  _atRenderSats();
  _atRenderPlan(snap);
  if (snap.computing) _atWaitForPlan();
}

function _atRenderSats() {
  var body = _atEl('auto-tbody'), filt = (_atEl('auto-filter').value || '').toLowerCase();
  var mod = _atEl('auto-modfilter').value;
  var def = _atEl('auto-minel').value || '';
  body.innerHTML = '';
  var shown = 0;
  _atSats.forEach(function (s) {
    if (mod && s.modules.indexOf(mod) < 0) return;
    if (filt && (s.name + ' ' + s.norad).toLowerCase().indexOf(filt) < 0) return;
    shown++;
    var st = _atSel[s.norad] || (_atSel[s.norad] = { on: false, min_el: null, down_mhz: null, module: '' });
    var tr = document.createElement('tr');

    var td = document.createElement('td');
    var cb = document.createElement('input'); cb.type = 'checkbox'; cb.checked = st.on;
    cb.onchange = function () { st.on = cb.checked; tr.classList.toggle('auto-on', st.on); };
    td.appendChild(cb); tr.appendChild(td);

    function text(t) { var d = document.createElement('td'); d.textContent = t; tr.appendChild(d); }
    text(s.name); text(s.norad); text(s.modules.join(', '));
    var stale = _atStale(s);
    var ageTd = document.createElement('td');
    ageTd.textContent = _atAgeText(s.age);
    if (stale) { ageTd.className = 'auto-stale'; ageTd.title = 'Element set too old - this satellite is ignored'; tr.classList.add('auto-old'); }
    tr.appendChild(ageTd);

    function num(key, ph, step) {
      var d = document.createElement('td');
      var i = document.createElement('input'); i.type = 'number'; i.step = step;
      i.placeholder = ph; i.className = 'auto-num';
      if (st[key] != null) i.value = st[key];
      i.oninput = function () {
        st[key] = i.value === '' ? null : parseFloat(i.value);
        if (i.value !== '' && !st.on) { st.on = true; cb.checked = true; tr.classList.add('auto-on'); }
      };
      d.appendChild(i); tr.appendChild(d);
    }
    num('min_el', def || 'default', '1');
    num('down_mhz', 'CAT off', '0.001');
    tr.classList.toggle('auto-on', st.on);
    tr.dataset.norad = s.norad;
    body.appendChild(tr);
  });
  var ticked = Object.keys(_atSel).filter(function (k) { return _atSel[k].on; });
  var oldTicked = ticked.filter(function (k) {
    return _atStale(_atSats.filter(function (x) { return x.norad === k; })[0]);
  }).length;
  _atEl('auto-count').textContent = shown + ' of ' + _atSats.length + ' satellites shown, '
    + ticked.length + ' ticked' + (oldTicked ? ' (' + oldTicked + ' with too old elements are ignored)' : '');
}

function atSelectShown(on) {
  document.querySelectorAll('#auto-tbody tr').forEach(function (tr) {
    var st = _atSel[tr.dataset.norad];
    var sat = _atSats.filter(function (x) { return x.norad === tr.dataset.norad; })[0];
    if (on && _atStale(sat)) return;            // "Tick shown" never ticks satellites that would be ignored
    if (st) { st.on = on; tr.querySelector('input[type=checkbox]').checked = on; tr.classList.toggle('auto-on', on); }
  });
  _atRenderSats();
}

function _atModuleOf(norad) {
  var s = _atSats.filter(function (x) { return x.norad === norad; })[0];
  var st = _atSel[norad];
  if (!s) return '';
  return (st && st.module && s.modules.indexOf(st.module) >= 0) ? st.module : s.modules[0];
}

function _atForm() {
  var entries = [];
  _atSats.forEach(function (s) {
    var st = _atSel[s.norad];
    if (st && st.on) {
      entries.push({ norad_id: s.norad, name: s.name, module: _atModuleOf(s.norad),
                     min_el: st.min_el, down_mhz: st.down_mhz, enabled: true });
    }
  });
  return {
    enabled:        _atEl('auto-enabled').checked,
    default_min_el: parseFloat(_atEl('auto-minel').value) || 0,
    lead_sec:       parseInt(_atEl('auto-lead').value, 10) || 0,
    tail_sec:       parseInt(_atEl('auto-tail').value, 10) || 0,
    use_rotor:      _atEl('auto-rotor').checked,
    use_cat:        _atEl('auto-cat').checked,
    max_tle_age_days: parseInt(_atEl('auto-maxage').value, 10) || 0,
    cmd_start:      _atEl('auto-cmd-start').value.trim(),
    cmd_end:        _atEl('auto-cmd-end').value.trim(),
    entries:        entries
  };
}

async function _atPost(body) {
  var r = await fetch('/api/autotrack', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

async function atSave() {
  var m = _atEl('auto-msg');
  try {
    var snap = await _atPost(_atForm());
    m.textContent = '💾 Saved'; m.style.color = 'var(--accent)';
    _atRenderPlan(snap);
    _atWaitForPlan();
  } catch (e) {
    m.textContent = '⚠ could not save (' + e.message + ')'; m.style.color = 'var(--warn)';
  }
}

async function atTestCmd(which) {
  var m = _atEl('auto-msg');
  try {
    await _atPost(_atForm());                       // the SAVED command is what runs
    var r = await fetch('/api/autotrack/test-command', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ which: which })
    });
    var d = await r.json();
    m.textContent = d.ok ? '▶ ' + which + ' command started with a dummy pass (TEST SAT)'
                         : '⚠ ' + (d.error || 'failed');
    m.style.color = d.ok ? 'var(--accent)' : 'var(--warn)';
  } catch (e) {
    m.textContent = '⚠ ' + e.message; m.style.color = 'var(--warn)';
  }
}

// The plan is rebuilt in the background; poll until it is ready.
async function _atWaitForPlan() {
  var gen = ++_atPoll;
  for (var i = 0; i < 12; i++) {
    await new Promise(function (r) { setTimeout(r, 1500); });
    if (gen !== _atPoll || !_atEl('dlg-auto').classList.contains('open')) return;
    try {
      var snap = await (await fetch('/api/autotrack')).json();
      _atRenderPlan(snap);
      if (!snap.computing) return;
    } catch (e) { return; }
  }
}

// ── Upcoming passes ──────────────────────────────────────────

function _atUtc(jd, fmt) {
  var d = new Date((jd - 2440587.5) * 86400000);
  var p = function (n) { return String(n).padStart(2, '0'); };
  var hm = p(d.getUTCHours()) + ':' + p(d.getUTCMinutes());
  return fmt === 'hm' ? hm : p(d.getUTCMonth() + 1) + '-' + p(d.getUTCDate()) + ' ' + hm;
}

function _atLen(sec) {
  if (sec >= 3600) return Math.floor(sec / 3600) + ' h ' + Math.round((sec % 3600) / 60) + ' min';
  return Math.floor(sec / 60) + ':' + String(Math.round(sec % 60)).padStart(2, '0');
}

function _atRenderPlan(snap) {
  var body = _atEl('auto-plan'), plan = snap.plan || [];
  body.innerHTML = '';
  plan.forEach(function (p) {
    var tr = document.createElement('tr');
    var isNow = snap.current && snap.current.norad_id === p.norad_id &&
                Math.abs(snap.current.aos_jd - p.aos_jd) < 0.015;
    function text(t, cls) { var d = document.createElement('td'); d.textContent = t; if (cls) d.className = cls; tr.appendChild(d); }
    text(_atUtc(p.aos_jd));
    text(p.name);
    text(p.max_el.toFixed(0) + '°');
    text(_atLen(p.duration));
    text(Math.round(p.aos_az) + '° → ' + Math.round(p.los_az) + '°');
    text(isNow ? '● tracking now' : (p.skipped ? '✕ ' + p.reason : '✓ will track'),
         isNow ? 'auto-now' : (p.skipped ? 'auto-skip' : 'auto-ok'));
    if (p.skipped) tr.classList.add('auto-skipped');
    body.appendChild(tr);
  });
  var note = _atEl('auto-plan-note');
  var lines = [];
  if (!snap.config.enabled) lines.push({ t: 'Auto-track is switched off – nothing will start (this is a preview).' });
  if (snap.computing) lines.push({ t: 'Planning…' });
  else if (!plan.length) lines.push({ t: 'No pass in the next 24 h reaches its threshold.' });
  (snap.problems || []).forEach(function (q) { lines.push({ t: q.name + ' – ' + q.reason, warn: !q.info }); });
  note.innerHTML = '';
  lines.forEach(function (l) {
    var d = document.createElement('div'); d.textContent = l.t;
    if (l.warn) d.className = 'auto-skip';
    note.appendChild(d);
  });
}

// ── Live: status chip + pass events from the server ─────────

function updateAutoStatus(s) {
  _atChip = s;
  _atDrawChip();
}

function _atDrawChip() {
  var chip = _atEl('sb-auto'), s = _atChip;
  if (!chip) return;
  if (!s || !s.enabled) { chip.style.display = 'none'; return; }
  chip.style.display = '';
  var nowJd = Date.now() / 86400000 + 2440587.5;
  if (s.current) {
    chip.textContent = '⏺ AUTO ' + s.current.name + ' → ' + _atUtc(s.current.los_jd, 'hm') + 'Z';
    chip.style.color = 'var(--accent)';
  } else if (s.next) {
    var mins = Math.max(0, Math.round((s.next.aos_jd - nowJd) * 1440));
    chip.textContent = '⏱ AUTO ' + s.next.name + ' ' + _atUtc(s.next.aos_jd, 'hm') + 'Z (in '
      + (mins >= 120 ? Math.round(mins / 60) + ' h' : mins + ' min') + ') ' + s.next.max_el.toFixed(0) + '°';
    chip.style.color = 'var(--fg-dim)';
  } else {
    chip.textContent = s.computing ? '⏱ AUTO planning…'
      : '⏱ AUTO: no pass ≥ ' + s.default_min_el + '° in 24 h';
    chip.style.color = 'var(--fg-dim)';
  }
}
setInterval(_atDrawChip, 5000);

// The server has already switched module / selection; bring the page in line with it.
async function _atSyncSelection(name) {
  try {
    var prev = _activeModule;
    await loadModuleList();
    if (_activeModule !== prev) {
      _modGen++;
      _followSat = null;
      if (typeof setFollowSat === 'function') setFollowSat(null);
      await loadSatList();
    }
    selectSat(name);
  } catch (e) { /* the server side works without the page */ }
}

function onAutoPass(msg) {
  var p = msg.pass || {};
  (msg.notes || []).forEach(function (n) { toast('⚠ Auto-track: ' + n, true); });
  if (msg.event === 'start') {
    toast('⏺ Auto-track: ' + p.name + ' (max ' + Math.round(p.max_el) + '°)');
    _atSyncSelection(p.name);
  } else {
    toast('■ Auto-track: ' + p.name + ' done');
  }
}
