// Kepler73 - polarmap.js v3
// Polar view: strobe points toward satellite, dot lights up when above horizon.
// With the rotator on, a solid green strobe + ring shows where it actually points,
// and an optional wedge shows the antenna beam.

var _polarPass      = null;
var _polarColor     = '#00e676';
var _polarSatName   = '';
var _polarAnimFrame = null;
var _polarCurrentAz = null;
var _polarCurrentEl = null;

// Rotator overlay state, fed by rotor.js (config) and the rotor_status event.
var _rotView = {
  show: true, beam: true, beamwidth: 20,         // from the rotor dialog
  enabled: false, connected: false, posOk: false, az: 0, el: 0
};
var ROT_COLOR = '#00e676';

function setRotorView(part) {
  Object.keys(part).forEach(function(k) { if (part[k] !== undefined) _rotView[k] = part[k]; });
  if (document.getElementById('polar-canvas')) {
    if (_polarAnimFrame) cancelAnimationFrame(_polarAnimFrame);
    _polarAnimFrame = requestAnimationFrame(drawPolar);
  }
}

// ── Public API ───────────────────────────────────────────────

function updatePolarMap(satName, color, passData) {
  _polarSatName = satName;
  _polarColor   = color || '#00e676';
  _polarPass    = passData;
  if (_polarAnimFrame) cancelAnimationFrame(_polarAnimFrame);
  _polarAnimFrame = requestAnimationFrame(drawPolar);
}

function clearPolarMap() {
  _polarPass      = null;
  _polarSatName   = '';
  _polarCurrentAz = null;
  _polarCurrentEl = null;
  if (_polarAnimFrame) cancelAnimationFrame(_polarAnimFrame);
  var canvas = document.getElementById('polar-canvas');
  var ctx    = canvas.getContext('2d');
  var cx = canvas.width / 2, cy = canvas.height / 2, R = cx - 16;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  drawGrid(ctx, cx, cy, R);
  drawRotor(ctx, cx, cy, R);
  document.getElementById('polar-hint').textContent = 'Select a satellite';
}

// Called from websocket.js on every position update
function updatePolarLive(az, el) {
  _polarCurrentAz = az;
  _polarCurrentEl = el;
  if (_polarAnimFrame) cancelAnimationFrame(_polarAnimFrame);
  _polarAnimFrame = requestAnimationFrame(drawPolar);
}

// ── Main draw ────────────────────────────────────────────────

function drawPolar() {
  var canvas = document.getElementById('polar-canvas');
  var ctx    = canvas.getContext('2d');
  var cx     = canvas.width  / 2;
  var cy     = canvas.height / 2;
  var R      = cx - 16;

  ctx.clearRect(0, 0, canvas.width, canvas.height);
  drawGrid(ctx, cx, cy, R);
  drawRotor(ctx, cx, cy, R);        // under the satellite strobe

  if (_polarCurrentAz === null) {
    document.getElementById('polar-hint').textContent = 'Select a satellite';
    return;
  }

  var az = _polarCurrentAz;
  var el = _polarCurrentEl;
  var aboveHorizon = (el !== null && el > 0);

  // ── Strobe line from center toward satellite ─────────────
  var rad = az * Math.PI / 180;

  ctx.beginPath();
  ctx.moveTo(cx, cy);
  ctx.lineTo(
    cx + Math.sin(rad) * R,
    cy - Math.cos(rad) * R
  );
  ctx.strokeStyle = aboveHorizon ? '#ffd54f' : '#2a5a2a';
  ctx.lineWidth   = aboveHorizon ? 1.5 : 1;
  ctx.setLineDash([4, 4]);
  ctx.stroke();
  ctx.setLineDash([]);

  // ── Satellite dot (only when above horizon) ──────────────
  if (aboveHorizon) {
    var r  = R * (1 - el / 90);
    var sx = cx + Math.sin(rad) * r;
    var sy = cy - Math.cos(rad) * r;

    // Glow
    ctx.beginPath();
    ctx.arc(sx, sy, 10, 0, Math.PI * 2);
    ctx.fillStyle = '#ffd54f33';
    ctx.fill();

    // Dot
    ctx.beginPath();
    ctx.arc(sx, sy, 5, 0, Math.PI * 2);
    ctx.fillStyle = '#ffd54f';
    ctx.fill();
  }

  // ── AZ / EL text – top right ─────────────────────────────
  ctx.font         = 'bold 10px monospace';
  ctx.textAlign    = 'right';
  ctx.textBaseline = 'top';
  ctx.fillStyle    = '#ffd54f';
  ctx.fillText('AZ: ' + az.toFixed(1) + '\xB0', canvas.width - 4, 4);
  ctx.fillText('EL: ' + (el !== null ? el.toFixed(1) : '---') + '\xB0', canvas.width - 4, 16);
  ctx.textAlign    = 'left';
  ctx.textBaseline = 'alphabetic';

  // ── Hint bar ─────────────────────────────────────────────
  var hint = _polarSatName;
  if (_polarPass && _polarPass.max_el) hint += '  \xB7  MAX ' + _polarPass.max_el + '\xB0';
  if (aboveHorizon) {
    hint += '  \xB7  IN VIEW';
  } else if (_polarPass && _polarPass.aos_dt) {
    hint += '  \xB7  AOS ' + _polarPass.aos_dt.slice(11, 16) + ' UTC';
  }
  document.getElementById('polar-hint').textContent = hint;
}

// ── Rotator overlay ──────────────────────────────────────────

// Outline of the beam cone (half-angle `half` degrees around az0/el0) projected like the
// grid: azimuth = angle, radius = 90 - elevation. Points come from the great-circle
// "destination point" formula with elevation as latitude, so the shape is exact
// (not a circle in this projection) and wraps correctly past the zenith.
function _beamOutline(cx, cy, R, az0, el0, half) {
  var D = Math.PI / 180;
  var d = half * D, lat1 = Math.min(el0, 89.9) * D, lon1 = az0 * D, pts = [];
  for (var i = 0; i < 72; i++) {
    var b = 2 * Math.PI * i / 72;
    var s2 = Math.sin(lat1) * Math.cos(d) + Math.cos(lat1) * Math.sin(d) * Math.cos(b);
    var lat2 = Math.asin(Math.max(-1, Math.min(1, s2)));
    var lon2 = lon1 + Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(lat1),
                                 Math.cos(d) - Math.sin(lat1) * s2);
    var r = R * (1 - (lat2 / D) / 90);
    pts.push([cx + Math.sin(lon2) * r, cy - Math.cos(lon2) * r]);
  }
  return pts;
}

function drawRotor(ctx, cx, cy, R) {
  var v = _rotView;
  if (!v.show || !v.enabled) return;

  ctx.font = 'bold 10px monospace';
  ctx.textAlign = 'left';
  ctx.textBaseline = 'top';

  if (!v.posOk) {                                   // nothing read back from the rotor yet
    ctx.fillStyle = v.connected ? '#5a6a5a' : '#ff9800';
    ctx.fillText(v.connected ? 'ROT: ...' : 'ROT: no link', 4, 4);
    ctx.textBaseline = 'alphabetic';
    return;
  }

  var live = v.connected;                           // stale = grey and dashed
  var rad  = v.az * Math.PI / 180;
  var ex   = cx + Math.sin(rad) * R, ey = cy - Math.cos(rad) * R;
  var col  = live ? ROT_COLOR : '#5a6a5a';

  // Beam wedge (clipped to the sky disc)
  if (v.beam && v.beamwidth > 0) {
    var pts = _beamOutline(cx, cy, R, v.az, Math.max(v.el, 0), v.beamwidth / 2);
    ctx.save();
    ctx.beginPath();
    ctx.arc(cx, cy, R, 0, Math.PI * 2);
    ctx.clip();
    ctx.beginPath();
    pts.forEach(function(p, i) { if (i) ctx.lineTo(p[0], p[1]); else ctx.moveTo(p[0], p[1]); });
    ctx.closePath();
    ctx.fillStyle   = live ? 'rgba(0,230,118,0.18)' : 'rgba(90,106,90,0.12)';
    ctx.fill();
    ctx.strokeStyle = live ? 'rgba(0,230,118,0.7)' : 'rgba(90,106,90,0.5)';
    ctx.lineWidth   = 1;
    ctx.stroke();
    ctx.restore();
  }

  // Pointing strobe: solid (the satellite strobe is dashed)
  ctx.save();
  ctx.beginPath();
  ctx.moveTo(cx, cy);
  ctx.lineTo(ex, ey);
  ctx.strokeStyle = col;
  ctx.lineWidth   = 2;
  if (live) { ctx.shadowColor = ROT_COLOR; ctx.shadowBlur = 6; } else { ctx.setLineDash([2, 4]); }
  ctx.stroke();
  ctx.restore();

  // Ring at the antenna elevation (the satellite is a filled dot)
  var rr = R * (1 - Math.max(0, Math.min(90, v.el)) / 90);
  ctx.beginPath();
  ctx.arc(cx + Math.sin(rad) * rr, cy - Math.cos(rad) * rr, 6, 0, Math.PI * 2);
  ctx.strokeStyle = col;
  ctx.lineWidth   = 2;
  ctx.stroke();

  ctx.fillStyle = col;
  ctx.fillText('ROT AZ: ' + v.az.toFixed(1) + '\xB0', 4, 4);
  ctx.fillText('ROT EL: ' + v.el.toFixed(1) + '\xB0', 4, 16);
  ctx.textBaseline = 'alphabetic';
}

// ── Grid ─────────────────────────────────────────────────────

function drawGrid(ctx, cx, cy, R) {
  if (R === undefined) { R = cx - 16; cy = cx; }

  ctx.fillStyle = '#060d06';
  ctx.beginPath();
  ctx.arc(cx, cy, R + 2, 0, Math.PI * 2);
  ctx.fill();

  [0, 30, 60, 90].forEach(function(el) {
    var r = R * (1 - el / 90);
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.strokeStyle = el === 0 ? '#1a4a1a' : '#122012';
    ctx.lineWidth   = el === 0 ? 1.5 : 1;
    ctx.stroke();
    if (el > 0 && el < 90) {
      ctx.fillStyle = '#2a5a2a';
      ctx.font      = '9px monospace';
      ctx.textAlign = 'left';
      ctx.fillText(el + '\xB0', cx + r + 2, cy - 2);
    }
  });

  var dirs = [
    { label: 'N', dx:  0, dy: -1 },
    { label: 'E', dx:  1, dy:  0 },
    { label: 'S', dx:  0, dy:  1 },
    { label: 'W', dx: -1, dy:  0 }
  ];
  dirs.forEach(function(d) {
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.lineTo(cx + d.dx * R, cy + d.dy * R);
    ctx.strokeStyle = '#122012';
    ctx.lineWidth   = 1;
    ctx.stroke();
    var lx = cx + d.dx * (R + 10);
    var ly = cy + d.dy * (R + 10);
    ctx.fillStyle    = d.label === 'N' ? '#00e676' : '#2a5a2a';
    ctx.font         = d.label === 'N' ? 'bold 11px monospace' : '10px monospace';
    ctx.textAlign    = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(d.label, lx, ly);
  });

  ctx.textAlign    = 'left';
  ctx.textBaseline = 'alphabetic';
}

function dateToJd(date) {
  return date.getTime() / 86400000 + 2440587.5;
}