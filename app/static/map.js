/**
 * GoWild Radar — Leaflet Map
 *
 * Fetches /api/results and draws route lines + destination markers on a dark map.
 * The home airport is shown as a pulsing green hub.
 * Available routes are drawn in green; unavailable in muted gray (toggleable).
 */

const HOME_AIRPORT = window.HOME_AIRPORT || 'ATL';

let mapInstance = null;
let routeLayer = null;
let showUnavailable = true;

function initMap() {
  const el = document.getElementById('map');
  if (!el || mapInstance) return;

  mapInstance = L.map('map', {
    zoomControl: false,
    attributionControl: true,
  }).setView([39.5, -98.35], 4);

  // Dark tile layer — CartoDB Dark Matter
  L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
    attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> © <a href="https://carto.com/attributions">CARTO</a>',
    subdomains: 'abcd',
    maxZoom: 19,
  }).addTo(mapInstance);

  // Zoom control — top right
  L.control.zoom({ position: 'topright' }).addTo(mapInstance);

  routeLayer = L.layerGroup().addTo(mapInstance);
}

// Custom marker icons
function makeHomeIcon() {
  return L.divIcon({
    className: '',
    iconSize: [24, 24],
    iconAnchor: [12, 12],
    html: `<div style="position:relative;width:24px;height:24px;display:flex;align-items:center;justify-content:center;">
             <div class="home-marker-ring"></div>
             <div class="home-marker-dot"></div>
           </div>`,
  });
}

function makeDestIcon(available) {
  const cls = available ? 'dest-marker-avail' : 'dest-marker-unavail';
  const size = available ? 14 : 11;
  return L.divIcon({
    className: '',
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
    html: `<div class="${cls}"></div>`,
  });
}

function buildPopupHtml(r) {
  const availHtml = r.gowild_available
    ? `<span class="popup-avail">✓ GoWild Available</span>`
    : `<span class="popup-unavail">✗ Not Available</span>`;

  const typeHtml = r.is_nonstop
    ? `<span style="color:#60a5fa">Nonstop</span>`
    : r.connection_info
    ? `<span style="color:#9ca3af">1 stop via ${r.connection_info}</span>`
    : '';

  const priceHtml = r.price_text
    ? `<div class="popup-price">${r.price_text}</div>`
    : '';

  const timeHtml =
    r.departure_time
      ? `<div>${r.departure_time}${r.arrival_time ? ' → ' + r.arrival_time : ''}</div>`
      : '';

  const lastChecked = r.checked_at
    ? `<div class="popup-meta">Checked ${timeAgoSimple(r.checked_at)}</div>`
    : '';

  const bookUrl = `https://www.flyfrontier.com/plan-trip/low-fare-calendar/?from=${r.origin}&to=${r.destination}&date=${r.departure_date || ''}`;
  const bookLink = r.gowild_available
    ? `<div style="margin-top:6px"><a href="${bookUrl}" target="_blank">Search on Frontier →</a></div>`
    : '';

  return `<div class="map-popup">
    <div class="popup-route">${r.origin} → ${r.destination}</div>
    <div>${availHtml}</div>
    ${r.departure_date ? `<div style="color:#9ca3af">${r.departure_date}</div>` : ''}
    ${timeHtml}
    ${priceHtml}
    ${typeHtml ? `<div>${typeHtml}</div>` : ''}
    ${bookLink}
    ${lastChecked}
  </div>`;
}

function timeAgoSimple(isoStr) {
  if (!isoStr) return '';
  const secs = Math.floor((Date.now() - new Date(isoStr)) / 1000);
  if (secs < 60) return 'just now';
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

/**
 * Main render function — called by dashboard.html after fetching results.
 * Accepts the JSON array from /api/results.
 */
window.renderMap = function(results) {
  if (!mapInstance) initMap();
  if (!routeLayer) return;

  routeLayer.clearLayers();

  // Group by destination — show best (most recent) result per dest
  const byDest = {};
  results.forEach(r => {
    if (!r.dest_lat || !r.dest_lon || !r.origin_lat || !r.origin_lon) return;
    const key = `${r.origin}-${r.destination}`;
    if (!byDest[key] || new Date(r.checked_at) > new Date(byDest[key].checked_at)) {
      byDest[key] = r;
    }
  });

  const destinations = Object.values(byDest);

  // Draw home airport hub (use coords from first result that matches HOME_AIRPORT)
  let homeCoords = null;
  const homeResult = destinations.find(r => r.origin === HOME_AIRPORT && r.origin_lat);
  if (homeResult) {
    homeCoords = [homeResult.origin_lat, homeResult.origin_lon];
  }

  if (homeCoords) {
    const homeMarker = L.marker(homeCoords, { icon: makeHomeIcon(), zIndexOffset: 1000 });
    homeMarker.bindPopup(`<div class="map-popup"><div class="popup-route">✈ ${HOME_AIRPORT}</div><div style="color:#9ca3af">Home Airport</div></div>`);
    routeLayer.addLayer(homeMarker);
  }

  const bounds = homeCoords ? [homeCoords] : [];

  destinations.forEach(r => {
    if (!showUnavailable && !r.gowild_available) return;

    const destCoords = [r.dest_lat, r.dest_lon];
    const originCoords = [r.origin_lat, r.origin_lon];
    bounds.push(destCoords);

    // Route line
    const lineColor = r.gowild_available ? '#22c55e' : '#374151';
    const lineWeight = r.gowild_available ? 2 : 1;
    const lineOpacity = r.gowild_available ? 0.75 : 0.25;
    const dashArray = r.gowild_available ? null : '4 6';

    const line = L.polyline([originCoords, destCoords], {
      color: lineColor,
      weight: lineWeight,
      opacity: lineOpacity,
      dashArray: dashArray,
    });
    routeLayer.addLayer(line);

    // Destination marker
    const marker = L.marker(destCoords, {
      icon: makeDestIcon(r.gowild_available),
      zIndexOffset: r.gowild_available ? 500 : 0,
    });
    marker.bindPopup(buildPopupHtml(r), { maxWidth: 240 });
    routeLayer.addLayer(marker);
  });

  // Fit map to visible routes (only on first load with data)
  if (bounds.length > 1 && !window._mapFitted) {
    try {
      mapInstance.fitBounds(bounds, { padding: [30, 30], maxZoom: 8 });
      window._mapFitted = true;
    } catch (e) { /* ignore invalid bounds */ }
  }
};

// Toggle unavailable routes
document.addEventListener('DOMContentLoaded', () => {
  initMap();

  const toggle = document.getElementById('toggle-unavailable');
  if (toggle) {
    toggle.addEventListener('change', e => {
      showUnavailable = e.target.checked;
      // Re-render with current data (will be re-fetched by dashboard.js)
      if (window._lastResults) window.renderMap(window._lastResults);
    });
  }
});

// Invalidate size when tab becomes visible (handles mobile browser quirks)
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && mapInstance) {
    setTimeout(() => mapInstance.invalidateSize(), 100);
  }
});
