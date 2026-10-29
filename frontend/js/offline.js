// Kepler73 - offline.js
// Offline-station features: the SatNOGS data pack (export on a computer with
// internet, import on the station), the TLE-sources status in Settings, and the
// About dialog with its (offline-aware) update check.
// Uses toast() from userdata.js.

function _dpDate(iso) { return iso ? String(iso).slice(0, 10) : '?'; }

// ── Data pack: export ───────────────────────────────────────

async function exportDataPack() {
  if (typeof closeAllMenus === 'function') closeAllMenus();
  toast('Preparing the data pack – downloading from SatNOGS, this can take up to a minute…');
  try {
    var res = await fetch('/api/datapack/export?refresh=1');
    if (!res.ok) {
      var err = {};
      try { err = await res.json(); } catch (e) {}
      toast(err.error || ('Export failed (' + res.status + ')'), true);
      return;
    }
    var info = {};
    try { info = JSON.parse(res.headers.get('X-Datapack-Info') || '{}'); } catch (e) {}
    var name = 'kepler73_datapack.zip';
    var m = /filename="([^"]+)"/.exec(res.headers.get('Content-Disposition') || '');
    if (m) name = m[1];

    var url = URL.createObjectURL(await res.blob());
    var a = document.createElement('a');
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 10000);

    var c = info.counts || {};
    if (info.refreshed === false) {
      toast((info.offline ? 'Offline station mode is on – ' : 'Could not reach SatNOGS – ') +
            name + ' holds the cached data from ' + _dpDate(info.data_fetched), true);
    } else {
      toast('Saved ' + name + ': ' + (c.satellites || 0) + ' satellites, ' +
            (c.transmitters || 0) + ' transmitters, SatNOGS data from ' +
            _dpDate(info.data_fetched));
    }
  } catch (e) {
    toast('Export failed: ' + e.message, true);
  }
}

// ── Data pack: import (a pack ZIP, or one raw SatNOGS API JSON file) ─

function importDataPackPick() {
  if (typeof closeAllMenus === 'function') closeAllMenus();
  var f = document.getElementById('datapack-file');
  f.value = '';
  f.click();
}

async function importDataPack(input) {
  var file = input.files && input.files[0];
  if (!file) return;
  toast('Installing ' + file.name + '…');
  var fd = new FormData();
  fd.append('file', file);
  try {
    var res = await fetch('/api/datapack/import', { method: 'POST', body: fd });
    var d = await res.json();
    if (!res.ok) { toast(d.error || 'Import failed', true); return; }

    var parts = (d.installed || []).map(function (k) { return d.counts[k] + ' ' + k; }).join(', ');
    toast('Installed ' + parts +
          (d.data_fetched ? ' – SatNOGS data from ' + _dpDate(d.data_fetched) : '') +
          (d.note ? ' – ' + d.note : ''), !!d.note);

    // Anything already showing SatNOGS data must reload.
    if (typeof _snRows !== 'undefined') {
      _snRows = null;
      if (document.getElementById('dlg-satnogs').classList.contains('open')) satnogsFetch(false);
    }
    if (typeof _txNorad !== 'undefined' && _txNorad) {
      loadTransponders(_txSatName, _txNorad, false);
    }
  } catch (e) {
    toast('Import failed: ' + e.message, true);
  }
}

// ── TLE sources (Settings) ──────────────────────────────────

function _renderTleSources(rows) {
  var box = document.getElementById('tle-src-status');
  if (!box) return;
  box.textContent = '';
  if (!rows || !rows.length) {
    box.textContent = 'No sources listed.';
    return;
  }
  rows.forEach(function (r) {
    var line = document.createElement('div');
    var mark = document.createElement('span');
    var good = !r.error;
    mark.className   = good ? 'ts-ok' : 'ts-bad';
    mark.textContent = good ? '✓ ' : '⚠ ';
    var what = good
      ? r.count + ' satellites' +
        (r.age_sec != null && typeof _fmtAge === 'function' ? ' · fetched ' + _fmtAge(r.age_sec) : '') +
        (r.kind === 'url' ? '' : ' · ' + r.kind)
      : r.error;
    var txt = document.createElement('span');
    txt.textContent = r.source + '  –  ' + what;     // textContent: sources are user-typed
    line.appendChild(mark);
    line.appendChild(txt);
    box.appendChild(line);
  });
}

async function loadTleSourceStatus() {
  try {
    var d = await (await fetch('/api/tle/sources')).json();
    _renderTleSources(d.sources);
  } catch (e) {}
}

// "Fetch / check sources now": uses the text in the box, saved or not.
async function fetchTleSources() {
  var box  = document.getElementById('tle-src-status');
  var spec = document.getElementById('cfg-tle-file').value;
  if (box) box.textContent = 'Fetching…';
  try {
    var res = await fetch('/api/tle/sources/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ spec: spec })
    });
    var d = await res.json();
    _renderTleSources(d.sources);
  } catch (e) {
    if (box) box.textContent = '⚠ ' + e.message;
  }
}

// After Settings are saved: if there are URL sources, fetch them right away.
async function afterTleSourcesSaved(spec) {
  if (!/^\s*https?:\/\//im.test(spec || '')) return;
  try {
    var res = await fetch('/api/tle/sources/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ spec: spec })
    });
    var d = await res.json();
    var bad = (d.sources || []).filter(function (r) { return r.error; });
    toast('TLE sources: ' + ((d.sources || []).length - bad.length) + ' ok' +
          (bad.length ? ', ' + bad.length + ' failed (see Settings)' : '') +
          ' – use Satellites → Update all TLEs to apply them', bad.length > 0);
  } catch (e) {}
}

// ── About dialog + update check ─────────────────────────────

async function showAboutDialog() {
  if (typeof closeAllMenus === 'function') closeAllMenus();
  var btn = document.getElementById('btn-update-check');
  var out = document.getElementById('update-result');
  out.textContent = '';
  try {
    var info = await (await fetch('/api/info')).json();
    document.getElementById('about-version').textContent =
      'v' + String(info.version || '').replace(/\.0$/, '');
    btn.disabled = !!info.offline_mode;
    btn.title    = info.offline_mode ? 'Not available in offline station mode' : '';
    if (info.offline_mode) out.textContent = 'Offline station mode – no update check.';
  } catch (e) {}
  openDialog('dlg-about');
}

async function checkForUpdate() {
  var btn = document.getElementById('btn-update-check');
  var out = document.getElementById('update-result');
  btn.disabled = true;
  out.style.color = '';
  out.textContent = 'Checking GitHub…';
  try {
    var d = await (await fetch('/api/update-check')).json();
    out.textContent = '';
    if (d.error) {
      out.style.color = 'var(--warn)';
      out.textContent = '⚠ ' + d.error;
    } else if (d.update_available) {
      out.style.color = 'var(--accent)';
      out.appendChild(document.createTextNode('⬆ Version ' + d.latest + ' is available (you have ' +
                                              d.current + ') – '));
      var a = document.createElement('a');
      a.href = d.url; a.target = '_blank'; a.rel = 'noopener';
      a.textContent = d.source === 'release' ? 'open the release on GitHub' : 'open the repository';
      out.appendChild(a);
    } else {
      out.style.color = 'var(--accent)';
      out.textContent = '✓ You are up to date (v' + d.current + ')' +
        (d.source === 'main' ? ' – no release published on GitHub yet' : '');
    }
  } catch (e) {
    out.style.color = 'var(--warn)';
    out.textContent = '⚠ ' + e.message;
  }
  var off = false;
  try { off = !!(await (await fetch('/api/info')).json()).offline_mode; } catch (e) {}
  btn.disabled = off;
}
