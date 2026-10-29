// Kepler73 - userdata.js
// Things the user enters themselves: satellite notes, manual transponders,
// export / import of both, and the "add a satellite" source chooser.

// Normalise a NORAD id the same way the backend does ("07530" -> "7530").
function _nk(id) {
  var n = parseInt(id, 10);
  return isNaN(n) ? String(id) : String(n);
}

// ── Toast (short non-blocking message) ──────────────────────

function toast(msg, isWarn) {
  var t = document.getElementById('toast');
  if (!t) {
    t = document.createElement('div');
    t.id = 'toast';
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.className = 'show' + (isWarn ? ' warn' : '');
  clearTimeout(toast._t);
  toast._t = setTimeout(function () { t.className = ''; }, 4500);
}

// ── Add satellite: pick the source ──────────────────────────

function showAddChoice() {
  if (typeof closeAllMenus === 'function') closeAllMenus();
  openDialog('dlg-add-choice');
}

function addChoice(which) {
  closeDialog('dlg-add-choice');
  if (which === 'satnogs') showSatnogsDialog();
  else showSearchDialog();
}

// ── Satellite notes ─────────────────────────────────────────

var _notes     = {};     // norad_id -> {text, flag, updated}
var _noteNorad = null;

async function loadNotes() {
  try {
    _notes = await (await fetch('/api/notes')).json();
  } catch (e) {
    _notes = {};
  }
  refreshNoteBadges();
}

// Update the 📝 / ❗ badges in the satellite list and the note row in the
// right panel. Safe to call any time (after a list reload, after an edit).
function refreshNoteBadges() {
  document.querySelectorAll('#sat-list .note-badge').forEach(function (b) {
    var n = _notes[_nk(b.dataset.norad)];
    b.textContent = n ? (n.flag ? '❗' : '📝') : '';
    b.title       = n ? n.text : '';
    b.classList.toggle('flag', !!(n && n.flag));
  });

  var box = document.getElementById('rp-note');
  var btn = document.getElementById('rp-note-btn');
  if (!box || !btn) return;
  var norad = (typeof _txNorad !== 'undefined' && _txNorad) ? _nk(_txNorad) : '';
  var note  = norad ? _notes[norad] : null;
  box.style.display = note ? '' : 'none';
  box.textContent   = note ? (note.flag ? '❗ ' : '📝 ') + note.text : '';
  btn.textContent   = note ? '📝 Edit note' : '📝 Add note';
}

function noteOpen(norad, name) {
  norad = _nk(norad);
  if (!name) {
    var it = document.querySelector('.sat-item[data-norad="' + norad + '"]');
    name = it ? it.dataset.name : ('NORAD ' + norad);
  }
  _noteNorad = norad;
  var n = _notes[norad];
  document.getElementById('note-sat').textContent    = name;
  document.getElementById('note-text').value         = n ? n.text : '';
  document.getElementById('note-flag').checked       = !!(n && n.flag);
  document.getElementById('note-delete').style.display = n ? '' : 'none';
  openDialog('dlg-note');
  setTimeout(function () { document.getElementById('note-text').focus(); }, 80);
}

function noteOpenForSelected() {
  if (typeof _txNorad !== 'undefined' && _txNorad) noteOpen(_txNorad, _txSatName);
}

function noteSave() {
  _noteSend(document.getElementById('note-text').value,
            document.getElementById('note-flag').checked);
}

function noteDelete() { _noteSend('', false); }

async function _noteSend(text, flag) {
  if (!_noteNorad) return;
  try {
    var res = await fetch('/api/notes/' + _noteNorad, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: text, flag: flag })
    });
    var d = await res.json();
    if (!res.ok) throw new Error(d.error || res.status);
    if (d.note) _notes[_noteNorad] = d.note; else delete _notes[_noteNorad];
    refreshNoteBadges();
    closeDialog('dlg-note');
  } catch (e) {
    toast('Could not save the note: ' + e.message, true);
  }
}

// A click on a badge opens the note instead of selecting the satellite.
(function () {
  var list = document.getElementById('sat-list');
  if (!list) return;
  list.addEventListener('click', function (e) {
    var b = e.target.closest ? e.target.closest('.note-badge') : null;
    if (b && b.textContent) {
      e.stopPropagation();
      noteOpen(b.dataset.norad);
    }
  }, true);
})();

// ── Manual transponders ─────────────────────────────────────

var _txeEditing = null;   // uuid being edited, or null when adding

function _mhz(hz) { return hz ? String(+(hz / 1e6).toFixed(6)) : ''; }

function _txeVal(id) { return document.getElementById(id).value.trim(); }

function txEditOpen(tx) {
  if (typeof _txNorad === 'undefined' || !_txNorad) {
    toast('Select a satellite first', true);
    return;
  }
  _txeEditing = tx ? tx.uuid : null;
  document.getElementById('txe-title').textContent =
    (tx ? 'Edit transponder – ' : 'Add transponder – ') + _txSatName;

  var set = function (id, v) { document.getElementById(id).value = (v == null ? '' : v); };
  set('txe-desc', tx ? tx.description : '');
  set('txe-dl',   tx ? _mhz(tx.downlink_hz) : '');
  set('txe-dlh',  tx ? _mhz(tx.downlink_high_hz) : '');
  set('txe-ul',   tx ? _mhz(tx.uplink_hz) : '');
  set('txe-ulh',  tx ? _mhz(tx.uplink_high_hz) : '');
  set('txe-mode', (tx && tx.mode && tx.mode !== '—') ? tx.mode : '');
  set('txe-baud', tx ? tx.baud : '');
  set('txe-note', tx ? tx.note : '');
  document.getElementById('txe-inv').checked    = tx ? !!tx.invert : false;
  document.getElementById('txe-active').checked = tx ? !!tx.active : true;
  document.getElementById('txe-delete').style.display = tx ? '' : 'none';
  document.getElementById('txe-msg').textContent = '';
  openDialog('dlg-tx-edit');
  setTimeout(function () { document.getElementById('txe-desc').focus(); }, 80);
}

async function txEditSave() {
  var msg  = document.getElementById('txe-msg');
  var body = {
    uuid:              _txeEditing,
    description:       _txeVal('txe-desc'),
    downlink_mhz:      _txeVal('txe-dl'),
    downlink_high_mhz: _txeVal('txe-dlh'),
    uplink_mhz:        _txeVal('txe-ul'),
    uplink_high_mhz:   _txeVal('txe-ulh'),
    mode:              _txeVal('txe-mode'),
    baud:              _txeVal('txe-baud'),
    note:              _txeVal('txe-note'),
    invert:            document.getElementById('txe-inv').checked,
    active:            document.getElementById('txe-active').checked
  };
  try {
    var res = await fetch('/api/transmitters/' + _nk(_txNorad) + '/custom', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    var d = await res.json();
    if (!res.ok) {
      msg.textContent = '⚠ ' + (d.error || 'could not save');
      msg.style.color = 'var(--warn)';
      return;
    }
    closeDialog('dlg-tx-edit');
    toast('Saved – marked as manual, kept across SatNOGS updates');
    loadTransponders(_txSatName, _txNorad, false);
  } catch (e) {
    msg.textContent = '⚠ ' + e.message;
    msg.style.color = 'var(--warn)';
  }
}

async function txEditDelete() {
  if (!_txeEditing || !confirm('Delete this manual transponder?')) return;
  try {
    var res = await fetch('/api/transmitters/' + _nk(_txNorad) + '/custom/' +
                          encodeURIComponent(_txeEditing), { method: 'DELETE' });
    if (!res.ok) throw new Error('not found');
    if (_txSelUuid === _txeEditing) {          // it was driving the CAT frequency
      _txSelUuid = null;
      _rigUseTransponder(null);
    }
    closeDialog('dlg-tx-edit');
    loadTransponders(_txSatName, _txNorad, false);
  } catch (e) {
    toast('Could not delete: ' + e.message, true);
  }
}

// ── Export / import (manual transponders + notes) ───────────

function exportUserData() {
  if (typeof closeAllMenus === 'function') closeAllMenus();
  // Content-Disposition: attachment – downloads without leaving the page.
  window.location.href = '/api/userdata/export';
}

function importUserDataPick() {
  if (typeof closeAllMenus === 'function') closeAllMenus();
  var f = document.getElementById('userdata-file');
  f.value = '';
  f.click();
}

async function importUserData(input) {
  var file = input.files && input.files[0];
  if (!file) return;
  var fd = new FormData();
  fd.append('file', file);
  try {
    var res = await fetch('/api/userdata/import', { method: 'POST', body: fd });
    var d = await res.json();
    if (!res.ok) { toast(d.error || 'Import failed', true); return; }
    toast('Imported ' + d.transponders_added + ' new + ' + d.transponders_updated +
          ' updated transponder(s) and ' + d.notes_imported + ' note(s)' +
          (d.skipped ? ' – ' + d.skipped + ' entries skipped' : ''));
    await loadNotes();
    if (typeof _txNorad !== 'undefined' && _txNorad) {
      loadTransponders(_txSatName, _txNorad, false);
    }
  } catch (e) {
    toast('Import failed: ' + e.message, true);
  }
}

// ── Init ────────────────────────────────────────────────────

loadNotes();
