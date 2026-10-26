const state = {
  db: null,
  map: null,
  clusterGroup: null,
  selectedPeriods: new Set(),
  allPeriods: [],
};

const els = {
  quarterControls: document.getElementById("quarterControls"),
  loadStatus: document.getElementById("loadStatus"),
  mapWrap: document.getElementById("mapWrap"),
};

function pinIcon() {
  return L.divIcon({
    className: "lmia-pin",
    html: '<span class="lmia-pin-dot"></span>',
    iconSize: [14, 14],
    iconAnchor: [7, 7],
  });
}

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

  state.clusterGroup = L.markerClusterGroup({
    maxClusterRadius: 50,
    spiderfyOnMaxZoom: true,
    iconCreateFunction: clusterIcon,
  });
  state.map.addLayer(state.clusterGroup);

  drawPins();
}

function clusterIcon(cluster) {
  const markers = cluster.getAllChildMarkers();
  const total = markers.reduce((sum, m) => sum + (m.options.positions || 0), 0);
  let size = "small";
  if (total > 5000) size = "large";
  else if (total > 500) size = "medium";
  return L.divIcon({
    html: `<div><span>${total.toLocaleString()}</span></div>`,
    className: `lmia-cluster lmia-cluster-${size}`,
    iconSize: [44, 44],
  });
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
    drawPins();
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
      drawPins();
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

function drawPins() {
  state.clusterGroup.clearLayers();
  if (state.selectedPeriods.size === 0) return;

  const placeholders = [...state.selectedPeriods].map(() => "?").join(",");
  const sql = `
    SELECT fsa, lat, lng, province, SUM(total_approved_positions) p, SUM(total_approved_lmias) l
    FROM lmia_map_points
    WHERE period IN (${placeholders})
    GROUP BY fsa, lat, lng, province
    HAVING p > 0
    ORDER BY p DESC`;
  const res = state.db.exec(sql, [...state.selectedPeriods]);
  const rows = res.length ? res[0].values : [];

  const markers = [];
  for (const [fsa, lat, lng, province, positions, lmias] of rows) {
    const marker = L.marker([lat, lng], { icon: pinIcon(), positions });
    marker.bindTooltip(`${fsa} — ${positions.toLocaleString()} positions`);
    marker.on("click", () => showLocationDetail(lat, lng, fsa, province, positions, lmias));
    markers.push(marker);
  }
  state.clusterGroup.addLayers(markers);
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
