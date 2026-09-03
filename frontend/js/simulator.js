// Kepler73 - simulator.js
// Pass simulator: scrub through any pass ±7 days

var _simPass       = null;   // current pass data
var _simPlaying    = false;
var _simInterval   = null;
var _simNorad      = null;
var _simSatName    = null;
var _simMarker     = null;   // Leaflet marker for simulated position

// ── Init date picker ─────────────────────────────────────────

function _isoDate(dt) {
  return dt.getFullYear() + '-' +
    String(dt.getMonth() + 1).padStart(2, '0') + '-' +
    String(dt.getDate()).padStart(2, '0');
}

function simInit() {
  var inp = document.getElementById('sim-date-input');
  if (!inp) return;
  var now = new Date();
  inp.value = _isoDate(now);
  var minD = new Date(now); minD.setFullYear(minD.getFullYear() - 1);
  var maxD = new Date(now); maxD.setDate(maxD.getDate() + 30);
  inp.min = _isoDate(minD);
  inp.max = _isoDate(maxD);
}

function simDayChanged() {}
function simTimeChanged() {}

// ── Find pass ────────────────────────────────────────────────

async function simFindPass() {
  var sat = getSelectedSat();
  if (!sat) {
    document.getElementById('sim-info').textContent = 'Select a satellite first';
    return;
  }

  var item = document.querySelector('.sat-item[data-name="' + sat + '"]');
  if (!item) return;
  _simNorad   = item.dataset.norad;
  _simSatName = sat;

  var day  = document.getElementById('sim-date-input').value;
  var time = document.getElementById('sim-time-input').value || '00:00';
  if (!day) {
    document.getElementById('sim-info').textContent = 'Pick a date first';
    return;
  }
  var dt   = day + 'T' + time + ':00';

  document.getElementById('sim-info').textContent = 'Searching...';
  document.getElementById('sim-controls').style.display = 'none';

  try {
    var res = await fetch('/api/passes/' + _simNorad + '/from?dt=' + encodeURIComponent(dt));
    if (!res.ok) {
      document.getElementById('sim-info').textContent = 'No pass found from this time';
      return;
    }
    _simPass = await res.json();
    simStopPlay();

    // Make pass available to savePassToFile()
    _lastPassData = { sat: _simSatName, norad: _simNorad, pass: _simPass };

    var aosDt = _simPass.aos_dt.slice(11,19);
    var losDt = _simPass.los_dt.slice(11,19);
    var info  = _simPass.aos_dt.slice(0,10) + '  ' + aosDt + ' → ' + losDt +
                ' UTC  |  MAX ' + _simPass.max_el + '°';

    // Warn if the element set used is far from the simulated date
    var ep = _simPass.tle_epoch_used;
    if (ep) {
      var days = Math.abs(
        (Date.parse(_simPass.aos_dt.slice(0,10)) - Date.parse(ep)) / 86400000);
      if (days > 10) {
        info += '  ⚠ TLE epoch ' + ep + ' (' + Math.round(days) +
                ' d away – load a TLE from near this date)';
      }
    }
    document.getElementById('sim-info').textContent = info;
    document.getElementById('sim-controls').style.display = 'block';
    document.getElementById('sim-slider').value = 0;

    // Show save button
    var saveBtn = document.getElementById('sim-save-btn');
    if (saveBtn) saveBtn.style.display = 'inline-block';

    simUpdate(0);

  } catch(e) {
    document.getElementById('sim-info').textContent = 'Error: ' + e.message;
  }
}

// ── Slider ───────────────────────────────────────────────────

function simSliderMove() {
  if (!_simPass) return;
  var pct = parseInt(document.getElementById('sim-slider').value);
  simUpdate(pct);
}

async function simUpdate(pct) {
  if (!_simPass) return;

  var frac  = pct / 100;
  var jd    = _simPass.aos_jd + frac * (_simPass.los_jd - _simPass.aos_jd);

  // Update time display
  var ms  = (jd - 2440587.5) * 86400000;
  var dt  = new Date(ms);
  var hh  = String(dt.getUTCHours()).padStart(2,'0');
  var mm  = String(dt.getUTCMinutes()).padStart(2,'0');
  var ss  = String(dt.getUTCSeconds()).padStart(2,'0');
  document.getElementById('sim-time-display').textContent =
    dt.getUTCFullYear() + '-' +
    String(dt.getUTCMonth()+1).padStart(2,'0') + '-' +
    String(dt.getUTCDate()).padStart(2,'0') + '  ' + hh + ':' + mm + ':' + ss + ' UTC';

  // Interpolate az/el from track
  var track = _simPass.track;
  if (track && track.length > 1) {
    var idx = Math.min(Math.floor(frac * (track.length-1)), track.length-2);
    var f2  = frac * (track.length-1) - idx;
    var az  = track[idx][0] + f2 * (track[idx+1][0] - track[idx][0]);
    var el  = track[idx][1] + f2 * (track[idx+1][1] - track[idx][1]);
    document.getElementById('sim-azel-display').textContent =
      'AZ: ' + az.toFixed(1) + '°  EL: ' + el.toFixed(1) + '°';

    // Update polar map
    updatePolarLive(az, el);
  }

  // Fetch exact lat/lon from backend and update map marker
  try {
    var posRes = await fetch('/api/passes/' + _simNorad + '/position?jd=' + jd);
    if (posRes.ok) {
      var pos = await posRes.json();
      simUpdateMapMarker(pos.lat, pos.lon, pos.az, pos.el);
    }
  } catch(e) {}
}

// ── Map marker ───────────────────────────────────────────────

function simUpdateMapMarker(lat, lon, az, el) {
  if (typeof map === 'undefined') return;

  var color = el > 0 ? '#ffd54f' : '#888888';
  var icon = L.divIcon({
    className: '',
    html: '<div style="width:14px;height:14px;border-radius:50%;background:' + color +
          ';border:2px solid #000;box-shadow:0 0 6px ' + color + '"></div>',
    iconSize: [14, 14],
    iconAnchor: [7, 7]
  });

  if (_simMarker) {
    _simMarker.setLatLng([lat, lon]);
    _simMarker.setIcon(icon);
  } else {
    _simMarker = L.marker([lat, lon], { icon: icon }).addTo(map);
  }
  _simMarker.bindTooltip('SIM: ' + _simSatName + '<br>AZ:' + az + '° EL:' + el + '°',
    { permanent: false });
}

function simClearMarker() {
  if (_simMarker && typeof map !== 'undefined') {
    map.removeLayer(_simMarker);
    _simMarker = null;
  }
}

// ── Play / Pause ─────────────────────────────────────────────

function simTogglePlay() {
  if (_simPlaying) {
    simStopPlay();
  } else {
    simStartPlay();
  }
}

function simStartPlay() {
  if (!_simPass) return;
  _simPlaying = true;
  document.getElementById('sim-play-btn').textContent = '⏸ Pause';
  _simInterval = setInterval(function() {
    var slider = document.getElementById('sim-slider');
    var val = parseInt(slider.value) + 1;
    if (val > 100) {
      simStopPlay();
      return;
    }
    slider.value = val;
    simUpdate(val);
  }, 200);  // ~5x realtime
}

function simStopPlay() {
  _simPlaying = false;
  if (_simInterval) clearInterval(_simInterval);
  _simInterval = null;
  var btn = document.getElementById('sim-play-btn');
  if (btn) btn.textContent = '▶ Play';
}

// ── Active / Inactive state ───────────────────────────────────

var _simActive = false;

function simSetActive(active) {
  _simActive = active;
  var container = document.getElementById('sim-container');
  var btn       = document.getElementById('sim-activate-btn');
  if (!container) return;
  if (active) {
    container.classList.remove('sim-inactive');
    if (btn) btn.textContent = '⏹ Deactivate';
  } else {
    container.classList.add('sim-inactive');
    if (btn) btn.textContent = '▶ Activate';
    simStopPlay();
    simClearMarker();
    _simPass = null;
    document.getElementById('sim-controls').style.display = 'none';
    document.getElementById('sim-info').textContent = 'Select satellite and date';
    var saveBtn = document.getElementById('sim-save-btn');
    if (saveBtn) saveBtn.style.display = 'none';
  }
}

function simToggleActive() {
  simSetActive(!_simActive);
}

// Call this when satellite changes to deactivate simulator
function simOnSatChange() {
  if (_simActive) simSetActive(false);
}

// ── Scroll wheel on slider ────────────────────────────────────

function simInitScroll() {
  var slider = document.getElementById('sim-slider');
  if (!slider) return;
  slider.addEventListener('wheel', function(e) {
    if (!_simActive || !_simPass) return;
    e.preventDefault();
    var delta = e.deltaY > 0 ? 1 : -1;
    var val = Math.max(0, Math.min(100, parseInt(slider.value) + delta));
    slider.value = val;
    simUpdate(val);
  }, { passive: false });
}

// ── Init on load ─────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', function() {
  simInit();
  simInitScroll();
  simSetActive(false);  // Start inactive
});