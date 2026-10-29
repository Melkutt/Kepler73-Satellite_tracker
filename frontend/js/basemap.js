// Kepler73 - basemap.js
// Built-in world map (Natural Earth 1:50m, public domain) drawn BELOW the tile layers.
// Online or with cached tiles the tiles cover it; on a station that never had map tiles
// (offline, empty cache) this IS the map: land, lakes and country borders at any zoom.
// Needs `map` from map.js.

(function () {
  if (typeof map === 'undefined' || typeof L === 'undefined') return;

  // A pane under the tile pane (z 200); Canvas keeps ~1.6 MB of polygons cheap to pan / zoom.
  map.createPane('basemap');
  var pane = map.getPane('basemap');
  pane.style.zIndex = 150;
  pane.style.pointerEvents = 'none';
  var renderer = L.canvas({ pane: 'basemap', padding: 0.5 });

  // Ocean is the dark map background; the tile layers are untouched.
  var STYLE = {
    land:    { color: '#2f8f4a', weight: 0.8, opacity: 0.9, fillColor: '#10281a', fillOpacity: 1 },
    lakes:   { color: '#2f8f4a', weight: 0.6, opacity: 0.7, fillColor: '#050a05', fillOpacity: 1 },
    borders: { color: '#3fae5e', weight: 0.6, opacity: 0.45, fill: false, dashArray: '3 3' }
  };

  var group   = L.layerGroup();
  var loaded  = false;
  var visible = true;
  try { visible = localStorage.getItem('basemap') !== '0'; } catch (e) {}

  // The tile layers repeat east and west of the date line, so draw the base map three times.
  function shifted(fc, dx) {
    function sh(c) { return typeof c[0] === 'number' ? [c[0] + dx, c[1]] : c.map(sh); }
    return {
      type: 'FeatureCollection',
      features: fc.features.map(function (f) {
        return { type: 'Feature', properties: {},
                 geometry: { type: f.geometry.type, coordinates: sh(f.geometry.coordinates) } };
      })
    };
  }

  function label() {
    var a = document.getElementById('menu-basemap');
    if (a) a.textContent = (visible ? '☑ ' : '☐ ') + 'Built-in offline map';
  }

  function apply() {
    if (visible && loaded) group.addTo(map); else map.removeLayer(group);
    label();
  }

  window.toggleBaseMap = function () {
    visible = !visible;
    try { localStorage.setItem('basemap', visible ? '1' : '0'); } catch (e) {}
    apply();
    if (typeof closeAllMenus === 'function') closeAllMenus();
  };

  async function load() {
    try {
      var names = ['land', 'lakes', 'borders'];
      var data  = await Promise.all(names.map(function (n) {
        return fetch('/vendor/naturalearth/' + n + '.json').then(function (r) { return r.json(); });
      }));
      names.forEach(function (n, i) {                 // order = paint order: land, lakes, borders
        [-360, 0, 360].forEach(function (dx) {
          L.geoJSON(dx ? shifted(data[i], dx) : data[i], {
            pane: 'basemap', renderer: renderer, style: STYLE[n],
            interactive: false, smoothFactor: 1.2
          }).addTo(group);
        });
      });
      loaded = true;
      apply();
    } catch (e) {
      console.warn('[Kepler73] built-in base map not available:', e);
    }
  }

  map.attributionControl.addAttribution(
    'Base map: <a href="https://www.naturalearthdata.com" target="_blank" rel="noopener">Natural Earth</a>');
  label();
  setTimeout(load, 0);          // after first paint – never delays the page
})();
