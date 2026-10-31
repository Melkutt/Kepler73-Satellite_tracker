// Kepler73 - map.js
// Leaflet map with OSM/Satellite layers, satellite markers and observer position

var osmLayer = L.tileLayer('/tiles/osm/{z}/{x}/{y}.png', {
  maxZoom: 19,
  attribution: '© <a href="https://openstreetmap.org">OpenStreetMap</a>'
});

var satLayer = L.tileLayer('/tiles/satellite/{z}/{x}/{y}.png', {
  maxZoom: 19,
  attribution: '© <a href="https://www.esri.com">Esri</a> World Imagery'
});

var map = L.map('map', {
  center: [20, 0],
  zoom: 3,
  layers: [satLayer],
  zoomControl: true
});

var currentLayer = 'satellite';
var satMarkers   = {};   // name -> { marker, tracks[], footprint }
var observerMarker = null;
var _followName  = null;  // satellite the map is locked onto, or null

// ── Map layers ───────────────────────────────────────────────

function setLayer(name) {
  if (name === currentLayer) return;
  if (name === 'satellite') {
    map.removeLayer(osmLayer);
    map.addLayer(satLayer);
    document.getElementById('btn-osm').classList.remove('active');
    document.getElementById('btn-sat').classList.add('active');
  } else {
    map.removeLayer(satLayer);
    map.addLayer(osmLayer);
    document.getElementById('btn-sat').classList.remove('active');
    document.getElementById('btn-osm').classList.add('active');
  }
  currentLayer = name;
}

function resetView() {
  map.setView([20, 0], 3, { animate: true, duration: 0.8 });
}

function zoomToSelected() {
  var sel = getSelectedSat();
  if (sel && satMarkers[sel]) {
    var latlng = satMarkers[sel].marker.getLatLng();
    map.setView(latlng, 6, { animate: true, duration: 0.8 });
  }
}

// ── Follow / lock map to a satellite ─────────────────────────

function setFollowSat(name) {
  _followName = name || null;
  if (_followName && satMarkers[_followName]) {
    var ll = satMarkers[_followName].marker.getLatLng();
    map.setView(ll, Math.max(map.getZoom(), 4), { animate: true, duration: 0.5 });
  }
}

function getFollowSat() { return _followName; }

// ── Observer marker ──────────────────────────────────────────

function updateObserverMarker(lat, lon) {
  var svg = '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">'
    + '<circle cx="12" cy="12" r="4" fill="#ffd54f" stroke="#000" stroke-width="1"/>'
    + '<line x1="12" y1="2"  x2="12" y2="7"  stroke="#ffd54f" stroke-width="2"/>'
    + '<line x1="12" y1="17" x2="12" y2="22" stroke="#ffd54f" stroke-width="2"/>'
    + '<line x1="2"  y1="12" x2="7"  y2="12" stroke="#ffd54f" stroke-width="2"/>'
    + '<line x1="17" y1="12" x2="22" y2="12" stroke="#ffd54f" stroke-width="2"/>'
    + '</svg>';

  var icon = L.divIcon({
    html: svg,
    className: '',
    iconSize: [24, 24],
    iconAnchor: [12, 12],
    tooltipAnchor: [14, 0]
  });

  if (observerMarker) {
    observerMarker.setLatLng([lat, lon]);
    observerMarker.setIcon(icon);
  } else {
    observerMarker = L.marker([lat, lon], { icon: icon, zIndexOffset: 2000 }).addTo(map);
    observerMarker.bindTooltip('Observer', {
      permanent: false,
      className: 'sat-label'
    });
  }
}

// Load observer position from API on startup
fetch('/api/config').then(function(r) { return r.json(); }).then(function(cfg) {
  updateObserverMarker(cfg.lat, cfg.lon);
});

// ── Satellite icon ───────────────────────────────────────────

function makeSatIcon(color, selected) {
  color = color || '#00e676';
  var size    = selected ? 24 : 20;
  var r       = selected ? 4.5 : 3.5;
  var stroke  = selected ? 2 : 1.5;
  var half    = size / 2;
  var svg = '<svg xmlns="http://www.w3.org/2000/svg" width="' + size + '" height="' + size + '" viewBox="0 0 ' + size + ' ' + size + '">'
    + '<circle cx="' + half + '" cy="' + half + '" r="' + r + '" fill="' + color + '" opacity="0.95"/>'
    + '<line x1="3"  y1="3"  x2="' + (half-2) + '" y2="' + (half-2) + '" stroke="' + color + '" stroke-width="' + stroke + '" stroke-linecap="round"/>'
    + '<line x1="' + (half+2) + '" y1="' + (half+2) + '" x2="' + (size-3) + '" y2="' + (size-3) + '" stroke="' + color + '" stroke-width="' + stroke + '" stroke-linecap="round"/>'
    + '<line x1="' + (half+2) + '" y1="3"  x2="' + (size-3) + '" y2="3"  stroke="' + color + '" stroke-width="' + stroke + '" stroke-linecap="round"/>'
    + '<line x1="3"  y1="' + (size-3) + '" x2="' + (half-2) + '" y2="' + (half+2) + '" stroke="' + color + '" stroke-width="' + stroke + '" stroke-linecap="round"/>'
    + '<line x1="' + (size-3) + '" y1="3"  x2="' + (half+2) + '" y2="' + (half-2) + '" stroke="' + color + '" stroke-width="' + stroke + '" stroke-linecap="round"/>'
    + '</svg>';
  return L.divIcon({
    html: svg,
    className: '',
    iconSize: [size, size],
    iconAnchor: [half, half],
    tooltipAnchor: [half + 2, 0]
  });
}

// ── Footprint outline ────────────────────────────────────────
// L.circle draws an ELLIPSE in the Mercator projection, which at high latitudes is neither
// the right shape nor in the right place: an observer just beyond the horizon looked
// "covered". This builds the real great-circle outline (radius_km along the surface) instead.

var FOOTPRINT_R_KM = 6378.137;   // same Earth radius as sgp4_engine.footprint_km()
var MERC_MAX_LAT   = 85;         // keep the path finite where Mercator blows up

function footprintLatLngs(lat, lon, radiusKm) {
  if (!(radiusKm > 0)) return [];
  var D = Math.PI / 180;
  var ang = radiusKm / FOOTPRINT_R_KM;                 // angular radius, radians
  var phi0 = lat * D, ring = [], i;
  var clampLat = function(v) { return Math.max(-MERC_MAX_LAT, Math.min(MERC_MAX_LAT, v)); };

  if (Math.abs(lat) + ang / D >= 90) {
    // The footprint contains a pole: the outline is a boundary latitude per longitude,
    // closed along the top (or bottom) edge of the map. Mirrored so the pole is "north".
    var sgn = lat >= 0 ? 1 : -1, p = Math.abs(phi0);
    for (var dl = -180; dl <= 180; dl += 3) {
      var a = Math.sin(p), b = Math.cos(p) * Math.cos(dl * D);
      var rr = Math.sqrt(a * a + b * b);
      var phi = Math.asin(Math.max(-1, Math.min(1, Math.cos(ang) / rr))) - Math.atan2(b, a);
      ring.push([clampLat(sgn * phi / D), lon + dl]);
    }
    ring.push([sgn * MERC_MAX_LAT, lon + 180]);
    ring.push([sgn * MERC_MAX_LAT, lon - 180]);
  } else {
    for (i = 0; i < 120; i++) {
      var brg = 2 * Math.PI * i / 120;
      var s2 = Math.sin(phi0) * Math.cos(ang) + Math.cos(phi0) * Math.sin(ang) * Math.cos(brg);
      var lat2 = Math.asin(Math.max(-1, Math.min(1, s2)));
      var dlon = Math.atan2(Math.sin(brg) * Math.sin(ang) * Math.cos(phi0),
                            Math.cos(ang) - Math.sin(phi0) * s2);
      ring.push([clampLat(lat2 / D), lon + dlon / D]);
    }
  }
  // Three world copies so a footprint over the date line shows on both sides.
  return [ring,
          ring.map(function(q) { return [q[0], q[1] + 360]; }),
          ring.map(function(q) { return [q[0], q[1] - 360]; })];
}

// ── Update satellites on map ─────────────────────────────────

function updateMapSatellites(data) {
  var names      = Object.keys(data);
  var selectedSat = getSelectedSat();

  // Remove markers for satellites no longer in data
  Object.keys(satMarkers).forEach(function(n) {
    if (!data[n]) {
      satMarkers[n].marker.remove();
      satMarkers[n].tracks.forEach(function(t) { t.remove(); });
      satMarkers[n].footprint.remove();
      delete satMarkers[n];
    }
  });

  names.forEach(function(name) {
    var s        = data[name];
    var latlng   = [s.lat, s.lon];
    var color    = s.color || '#00e676';
    var selected = (name === selectedSat);

    if (!satMarkers[name]) {
      // Create new marker
      var marker = L.marker(latlng, {
        icon: makeSatIcon(color, selected),
        zIndexOffset: selected ? 2000 : 1000
      }).addTo(map);

      marker.bindTooltip(name, {
        permanent: true,
        direction: 'right',
        className: 'sat-label',
        offset: [10, 0]
      });

      marker.on('click', function() { selectSat(name); });

      var footprint = L.polygon(footprintLatLngs(s.lat, s.lon, s.footprint_km), {
        interactive: false,
        color: color,
        weight: 1,
        opacity: selected ? 0.5 : 0.2,
        fillColor: color,
        fillOpacity: selected ? 0.12 : 0.03
      }).addTo(map);

      satMarkers[name] = { marker: marker, tracks: [], footprint: footprint };

    } else {
      // Update existing marker
      satMarkers[name].marker.setLatLng(latlng);
      satMarkers[name].marker.setIcon(makeSatIcon(color, selected));
      satMarkers[name].marker.setZIndexOffset(selected ? 2000 : 1000);
      satMarkers[name].footprint.setStyle({
        opacity:     selected ? 0.5  : 0.2,
        fillOpacity: selected ? 0.12 : 0.03
      });
      satMarkers[name].footprint.setLatLngs(footprintLatLngs(s.lat, s.lon, s.footprint_km));
    }

    // Redraw ground track - remove old segments, draw new ones
    satMarkers[name].tracks.forEach(function(t) { t.remove(); });
    satMarkers[name].tracks = [];

    if (s.track && s.track.length > 1) {
      var segments = splitAntimeridian(s.track);
      segments.forEach(function(seg) {
        if (seg.length > 1) {
          var line = L.polyline(seg, {
            color:     color,
            weight:    selected ? 2.5 : 1.5,
            opacity:   selected ? 0.8 : 0.5,
            dashArray: selected ? null : '5 5'
          }).addTo(map);
          satMarkers[name].tracks.push(line);
        }
      });
    }
  });

  // Keep the map locked on the followed satellite
  if (_followName && data[_followName]) {
    var f = data[_followName];
    map.panTo([f.lat, f.lon], { animate: true, duration: 0.5, noMoveStart: true });
  }
}

// ── Antimeridian handling ────────────────────────────────────

function splitAntimeridian(points) {
  // Split track at the antimeridian (+/-180 deg) to avoid
  // lines drawing across the entire map
  var segments = [[]];
  for (var i = 0; i < points.length; i++) {
    if (i > 0 && Math.abs(points[i][1] - points[i-1][1]) > 180) {
      segments.push([]);
    }
    segments[segments.length - 1].push(points[i]);
  }
  return segments;
}