/* Kohteet kartalla (app/templates/targets/map.html). Markers come from a JSON
   block on the page; popups are built with textContent, never innerHTML, so
   listing texts cannot inject markup. */
(function () {
  'use strict';
  var container = document.getElementById('targets-map');
  var dataEl = document.getElementById('targets-map-data');
  if (!container || !dataEl || typeof L === 'undefined') return;
  var points = JSON.parse(dataEl.textContent || '[]');
  if (!points.length) return;

  var COLORS = { high: '#059669', good: '#6846DC', mid: '#EA580C', low: '#6B7280' };
  var map = L.map(container, { scrollWheelZoom: true, preferCanvas: false });
  map.attributionControl.setPrefix('<a href="https://leafletjs.com" target="_blank" rel="noopener">Leaflet</a>');
  var tileUrl = container.dataset.tileUrl;
  if (tileUrl) {
    var attributionEl = document.getElementById('targets-map-attribution');
    L.tileLayer(tileUrl, {
      maxZoom: 18,
      attribution: attributionEl ? JSON.parse(attributionEl.textContent || '""') : ''
    }).addTo(map);
  }

  var cardUrl = container.dataset.cardUrl; // .../housing-companies/0?...
  function cardLink(listingId) {
    return cardUrl.replace('/housing-companies/0', '/housing-companies/' + listingId);
  }

  function popup(point) {
    var box = document.createElement('div');
    box.className = 'targets-map__popup';
    var title = document.createElement('a');
    title.href = cardLink(point.listing_id);
    title.textContent = point.title;
    title.className = 'targets-map__popup-title';
    box.appendChild(title);
    function line(text, cls) {
      if (!text) return;
      var p = document.createElement('div');
      p.textContent = text;
      if (cls) p.className = cls;
      box.appendChild(p);
    }
    line(point.location);
    line('Pisteet ' + point.score + '/100', 'targets-map__popup-score');
    line(point.renovations.join(' · '));
    line(point.manager ? 'Isännöinti: ' + point.manager + (point.has_contact ? '' : ' (ei yhteystietoja)') : '');
    line('Sijainti: ' + point.precision_label, 'targets-map__popup-muted');
    return box;
  }

  var cluster = typeof L.markerClusterGroup === 'function'
    ? L.markerClusterGroup({ showCoverageOnHover: false, maxClusterRadius: 45 })
    : L.layerGroup();
  var bounds = [];
  points.forEach(function (point) {
    var approximate = point.precision === 'city' || point.precision === 'region';
    var marker = L.circleMarker([point.lat, point.lon], {
      radius: 8,
      weight: approximate ? 2 : 1.5,
      color: approximate ? '#111827' : '#fff',
      dashArray: approximate ? '3 3' : null,
      fillColor: COLORS[point.level] || COLORS.low,
      fillOpacity: approximate ? 0.65 : 0.9
    });
    marker.bindPopup(function () { return popup(point); });
    marker.bindTooltip(point.title + ' · ' + point.score + '/100');
    cluster.addLayer(marker);
    bounds.push([point.lat, point.lon]);
  });
  cluster.addTo(map);
  if (bounds.length === 1) {
    map.setView(bounds[0], 12);
  } else {
    map.fitBounds(bounds, { padding: [30, 30], maxZoom: 13 });
  }
})();
