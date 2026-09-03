// Kepler73 - websocket.js
// Socket.IO client - receives live positions and alarm events from Flask

var socket = io();

socket.on('connect', function() {
  console.log('[Kepler73] WebSocket connected');
});

socket.on('disconnect', function() {
  console.log('[Kepler73] WebSocket disconnected');
});

socket.on('satellite_positions', function(data) {
  updateMapSatellites(data);
  updateSidebarPositions(data);
  updateDoppler(data);

  // Update polar strobe for selected satellite
  Object.keys(data).forEach(function(name) {
    var sat = data[name];
    if (sat && (sat.selected || name === _selectedSat)) {
      updatePolarLive(sat.az, sat.el);
    }
  });
});

socket.on('pass_alarm', function(data) {
  triggerAlarmUI(data);
});

// ── Selected satellite - tells server to compute 3-orbit track ──

function notifySelected(name) {
  socket.emit('set_selected', { name: name || '' });
}

// ── Mute control ─────────────────────────────────────────────

var _muted = false;

function toggleMute() {
  _muted = !_muted;
  socket.emit('set_muted', { muted: _muted });
  var btn = document.getElementById('btn-mute');
  btn.textContent = _muted ? '🔇' : '🔊';
  btn.classList.toggle('muted', _muted);
}

// ── Alarm minutes ────────────────────────────────────────────

function sendAlarmMinutes(minutes) {
  socket.emit('set_alarm_minutes', { minutes: parseInt(minutes) });
}

function sendAlarmSound(sound) {
  socket.emit('set_alarm_sound', { sound: sound });
}

// ── Heartbeat – tells server browser is still open ───────────
setInterval(function() {
  fetch('/api/heartbeat', { method: 'POST' }).catch(function() {});
}, 30000);
// Send immediately on load
fetch('/api/heartbeat', { method: 'POST' }).catch(function() {});

function updateClock() {
  var now = new Date();
  var hh = String(now.getUTCHours()).padStart(2, '0');
  var mm = String(now.getUTCMinutes()).padStart(2, '0');
  var ss = String(now.getUTCSeconds()).padStart(2, '0');
  document.getElementById('status-clock').textContent = hh + ':' + mm + ':' + ss + ' UTC';
}

setInterval(updateClock, 1000);
updateClock();