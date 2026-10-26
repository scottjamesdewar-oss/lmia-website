const ZOOM_SWITCH = 6; // below this: province circles. at/above: FSA circles.

const PROVINCE_CENTROIDS = {
  "Alberta": [53.9333, -116.5765],
  "British Columbia": [53.7267, -127.6476],
  "Manitoba": [53.7609, -98.8139],
  "New Brunswick": [46.5653, -66.4619],
  "Newfoundland and Labrador": [53.1355, -57.6604],
  "Nova Scotia": [44.682, -63.7443],
  "Ontario": [51.2538, -85.3232],
  "Prince Edward Island": [46.5107, -63.4168],
  "Quebec": [52.9399, -73.5491],
  "Saskatchewan": [52.9399, -106.4509],
  "Northwest Territories": [64.8255, -124.8457],
  "Nunavut": [70.2998, -83.1076],
  "Yukon": [64.2823, -135.0],
};

const state = {
  db: null,
  map: null,
  layer: null,
  selectedPeriods: new Set(),
  allPeriods: [],
  redrawTimer: null,
};

const els = {
  quarterControls: document.getElementById("quarterControls"),
  loadStatus: document.getElementById("loadStatus"),
  mapWrap: document.getElementById("mapWrap"),
  mapLegend: document.getElementById("mapLegend"),
};

async function init() {
  const db = await loadDatabase((received, total) => {
    if (total) {
      const mb = (received / 1e6).toFixed(1);
      const totalMb = (total / 1e6).toFixed(1);
      els.loadStatus.textContent = `Loading database… ${mb} / ${totalMb} MB`;
    }
  });
  state.db = db;
  els.loadStatus.hidden = true;

  const res = db.exec("SELECT DISTINCT period FROM lmia_map_points ORDER BY period");
  state.allPeriods = res.length ? res[0].values.map((r) => r[0]) : [];
  state.selectedPeriods = new Set(state.allPeriods);

  buildQuarterControls();

  state.map = L.map(els.mapWrap).setView([56.1, -106.3], 4);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 15,
    attribution: '&copy; OpenStreetMap contributors',
  }).addTo(state.map);
  state.layer = L.layerGroup().addTo(state.map);

  state.map.on("zoomend moveend", () => {
    clearTimeout(state.redrawTimer);
    state.redrawTimer = setTimeout(drawCircles, 150);
  });

  drawCircles();
}

function buildQuarterControls() {
  els.quarterControls.innerHTML = "";

  const allBtn = document.createElement("button");
  allBtn.type = "button";
  allBtn.textContent = "All quarters";
  allBtn.className = "active";
  allBtn.addEventListener("click", () => {
    state.selectedPeriods = new Set(state.allPeriods);
    syncButtonStates();
    drawCircles();
  });
  els.quarterControls.appendChild(allBtn);

  for (const period of state.allPeriods) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = period;
    btn.dataset.period = period;
    btn.className = "active";
    btn.addEventListener("click", () => {
      if (state.selectedPeriods.has(period)) {
        state.selectedPeriods.delete(period);
      } else {
        state.selectedPeriods.add(period);
      }
      syncButtonStates();
      drawCircles();
    });
    els.quarterControls.appendChild(btn);
  }
}

function syncButtonStates() {
  const buttons = els.quarterControls.querySelectorAll("button[data-period]");
  for (const btn of buttons) {
    btn.classList.toggle("active", state.selectedPeriods.has(btn.dataset.period));
  }
  const allBtn = els.quarterControls.querySelector("button:not([data-period])");
  allBtn.classList.toggle("active", state.selectedPeriods.size === state.allPeriods.length);
}

function radiusForPositions(positions, maxR) {
  const minR = 4;
  const scaled = Math.sqrt(positions) * 2.2;
  return Math.max(minR, Math.min(maxR, scaled));
}

function drawCircles() {
  state.layer.clearLayers();
  if (state.selectedPeriods.size === 0 || !state.map) return;

  const zoom = state.map.getZoom();
  if (zoom < ZOOM_SWITCH) {
    drawProvinceCircles();
    if (els.mapLegend) {
      els.mapLegend.textContent =
        "Zoomed out: circles are grouped by province to avoid overlapping postal-code areas. Zoom in to see individual postal-code areas.";
    }
  } else {
    drawFsaCircles();
    if (els.mapLegend) {
      els.mapLegend.textContent =
        "Circle area is proportional to total approved positions for the selected quarter(s), grouped by postal-code area (FSA). Click a circle for details.";
    }
  }
}

function drawProvinceCircles() {
  const placeholders = [...state.selectedPeriods].map(() => "?").join(",");
  const sql = `
    SELECT province, SUM(total_approved_positions) p, SUM(total_approved_lmias) l
    FROM lmia_map_points
    WHERE period IN (${placeholders})
    GROUP BY province
    HAVING p > 0
    ORDER BY p DESC`;
  const res = state.db.exec(sql, [...state.selectedPeriods]);
  const rows = res.length ? res[0].values : [];

  for (const [province, positions, lmias] of rows) {
    const centroid = PROVINCE_CENTROIDS[province];
    if (!centroid) continue;
    const circle = L.circleMarker(centroid, {
      radius: radiusForPositions(positions, 70),
      color: "#e4572e",
      weight: 1,
      fillColor: "#e4572e",
      fillOpacity: 0.45,
    });
    circle.bindTooltip(`${province} — ${positions.toLocaleString()} positions (zoom in for detail)`);
    circle.on("click", () => showProvinceDetail(centroid, province, positions, lmias));
    circle.addTo(state.layer);
  }
}

function drawFsaCircles() {
  const bounds = state.map.getBounds().pad(0.2);
  const placeholders = [...state.selectedPeriods].map(() => "?").join(",");
  const sql = `
    SELECT fsa, lat, lng, province, SUM(total_approved_positions) p, SUM(total_approved_lmias) l
    FROM lmia_map_points
    WHERE period IN (${placeholders})
      AND lat BETWEEN ? AND ?
      AND lng BETWEEN ? AND ?
    GROUP BY fsa, lat, lng, province
    HAVING p > 0
    ORDER BY p DESC`;
  const params = [
    ...state.selectedPeriods,
    bounds.getSouth(),
    bounds.getNorth(),
    bounds.getWest(),
    bounds.getEast(),
  ];
  const res = state.db.exec(sql, params);
  const rows = res.length ? res[0].values : [];

  for (const [fsa, lat, lng, province, positions, lmias] of rows) {
    const circle = L.circleMarker([lat, lng], {
      radius: radiusForPositions(positions, 40),
      color: "#e4572e",
      weight: 1,
      fillColor: "#e4572e",
      fillOpacity: 0.45,
    });
    circle.bindTooltip(`${fsa} — ${positions.toLocaleString()} positions`);
    circle.on("click", () => showLocationDetail(lat, lng, fsa, province, positions, lmias));
    circle.addTo(state.layer);
  }
}

function showProvinceDetail(centroid, province, positions, lmias) {
  const placeholders = [...state.selectedPeriods].map(() => "?").join(",");
  const sql = `
    SELECT fsa, SUM(total_approved_positions) p
    FROM lmia_map_points
    WHERE province = ? AND period IN (${placeholders})
    GROUP BY fsa
    HAVING p > 0
    ORDER BY p DESC
    LIMIT 10`;
  const res = state.db.exec(sql, [province, ...state.selectedPeriods]);
  const rows = res.length ? res[0].values : [];

  let html = `<strong>${province}</strong><br>
    ${positions.toLocaleString()} approved positions / ${lmias.toLocaleString()} approved LMIAs
    in the selected quarter(s)<br><br>
    <em>Top postal-code areas in this province:</em>
    <table>`;
  for (const [fsa, fsaPositions] of rows) {
    html += `<tr><td>${escapeHtml(fsa)}</td><td>${fsaPositions.toLocaleString()} pos.</td></tr>`;
  }
  html += `</table><br><em>Zoom in on the map to see individual postal-code circles.</em>`;

  L.popup({ maxWidth: 320 }).setLatLng(centroid).setContent(html).openOn(state.map);
}

function showLocationDetail(lat, lng, fsa, province, positions, lmias) {
  const res = state.db.exec(
    `SELECT employer, address, total_approved_lmias, total_approved_positions, is_noncompliant
     FROM lmia_employers
     WHERE fsa = ?
     ORDER BY total_approved_positions DESC
     LIMIT 15`,
    [fsa]
  );
  const rows = res.length ? res[0].values : [];

  let html = `<strong>${fsa}, ${province}</strong><br>
    ${positions.toLocaleString()} approved positions / ${lmias.toLocaleString()} approved LMIAs
    in the selected quarter(s)<br>
    <span style="color:#9aa1ad">Postal-code areas can span several city blocks, so unrelated
    businesses sharing the same first-3-character postal code (e.g. ${fsa}) appear together here.</span><br><br>
    <em>Top employers at this location (all-time):</em>
    <table>`;
  for (const [employer, address, empLmias, empPositions, isNc] of rows) {
    const flag = isNc ? ' <span style="color:#ef4444">⚠</span>' : "";
    html += `<tr><td>${escapeHtml(employer)}${flag}</td><td>${empPositions.toLocaleString()} pos.</td></tr>`;
  }
  html += `</table><br><a href="lmia.html" target="_blank">See full LMIA Database →</a>`;

  L.popup({ maxWidth: 320 }).setLatLng([lat, lng]).setContent(html).openOn(state.map);
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

init().catch((err) => {
  els.loadStatus.hidden = false;
  els.loadStatus.textContent = `Could not load database: ${err.message}`;
  console.error(err);
});
