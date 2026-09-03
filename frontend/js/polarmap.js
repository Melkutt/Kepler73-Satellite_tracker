// Kepler73 - polarmap.js v2
// Polar view: strobe points toward satellite, dot lights up when above horizon

var _polarPass      = null;
var _polarColor     = '#00e676';
var _polarSatName   = '';
var _polarAnimFrame = null;
var _polarCurrentAz = null;
var _polarCurrentEl = null;

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