const PALETTE = [
  "#e4572e", "#4c9f70", "#4a7ab5", "#c9a227", "#9b5de5",
  "#2fbf71", "#e85d75", "#5aa9e6", "#f4a261", "#7f7f7f",
];

const els = {
  loadStatus: document.getElementById("loadStatus"),
  chartsRoot: document.getElementById("chartsRoot"),
  drillOverlay: document.getElementById("drillOverlay"),
  drillBreadcrumb: document.getElementById("drillBreadcrumb"),
  drillStatus: document.getElementById("drillStatus"),
  drillList: document.getElementById("drillList"),
  drillClose: document.getElementById("drillClose"),
  drillPagination: document.getElementById("drillPagination"),
  drillPrev: document.getElementById("drillPrev"),
  drillNext: document.getElementById("drillNext"),
  drillPageLabel: document.getElementById("drillPageLabel"),
};

const DRILL_PAGE_SIZE = 50;

function isDark() {
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

function baseOptions(extra) {
  const textColor = isDark() ? "#9aa1ad" : "#5b6270";
  const gridColor = isDark() ? "#2a2f3a" : "#e0e2e7";
  return Object.assign(
    {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { labels: { color: textColor } },
        tooltip: { mode: "index", intersect: false },
      },
      scales: {
        x: { ticks: { color: textColor }, grid: { color: gridColor } },
        y: { ticks: { color: textColor }, grid: { color: gridColor } },
      },
    },
    extra || {}
  );
}

function truncate(text, max) {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

async function init() {
  const db = await loadDatabase((received, total) => {
    if (total) {
      const mb = (received / 1e6).toFixed(1);
      const totalMb = (total / 1e6).toFixed(1);
      els.loadStatus.textContent = `Loading database… ${mb} / ${totalMb} MB`;
    }
  });
  els.loadStatus.hidden = true;
  els.chartsRoot.hidden = false;

  drawVolumeChart(db);
  drawLmiaCountByOccupationChart(db);
  drawFranchiseChart(db);
  drawOccupationChart(db);
  drawStreamShareChart(db);
  drawProvinceChart(db);
}

function cleanOccupationTitle(occupation) {
  const i = occupation.indexOf("-");
  return i > 0 ? occupation.slice(i + 1) : occupation;
}

// --- Drill-down overlay: Year -> ("Other" ->) Occupation -> Employers ---

const drillState = { path: [] };

function startDrillDown(rootPath, nextLabel, nextRender) {
  drillState.path = [...rootPath, { label: nextLabel, render: nextRender }];
  els.drillOverlay.hidden = false;
  renderBreadcrumb();
  nextRender();
}

function pushDrillLevel(label, render) {
  drillState.path.push({ label, render });
  renderBreadcrumb();
  render();
}

function popToDrillLevel(index) {
  drillState.path = drillState.path.slice(0, index + 1);
  renderBreadcrumb();
  const entry = drillState.path[index];
  if (entry.render) entry.render();
}

function closeDrillDown() {
  els.drillOverlay.hidden = true;
  drillState.path = [];
  els.drillList.innerHTML = "";
  els.drillPagination.hidden = true;
}

function renderBreadcrumb() {
  els.drillBreadcrumb.innerHTML = "";
  drillState.path.forEach((entry, i) => {
    if (i > 0) {
      const sep = document.createElement("span");
      sep.className = "sep";
      sep.textContent = "›";
      els.drillBreadcrumb.appendChild(sep);
    }
    const isLast = i === drillState.path.length - 1;
    if (isLast || !entry.render) {
      const span = document.createElement("span");
      span.textContent = entry.label;
      els.drillBreadcrumb.appendChild(span);
    } else {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = entry.label;
      btn.addEventListener("click", () => popToDrillLevel(i));
      els.drillBreadcrumb.appendChild(btn);
    }
  });
}

function renderOccupationsLevel({ db, valueColumn, valueLabel, year, byYear, topSet }) {
  els.drillPagination.hidden = true;
  const yearMap = byYear.get(year);
  const rows = [...yearMap.entries()]
    .filter(([title]) => !topSet.has(title))
    .sort((a, b) => b[1] - a[1]);

  els.drillStatus.textContent = `${rows.length.toLocaleString()} occupations in "Other" for ${year}`;
  els.drillList.innerHTML = "";
  const frag = document.createDocumentFragment();
  for (const [title, value] of rows) {
    const row = document.createElement("div");
    row.className = "drill-row";
    row.innerHTML = `
      <div class="drill-row-main"><div class="drill-row-name"></div></div>
      <div class="drill-row-value"></div>`;
    row.querySelector(".drill-row-name").textContent = title;
    row.querySelector(".drill-row-value").textContent = `${value.toLocaleString()} ${valueLabel}`;
    row.addEventListener("click", () => {
      pushDrillLevel(title, () =>
        renderBusinessesLevel({ db, valueColumn, valueLabel, year, occupation: title, page: 0 })
      );
    });
    frag.appendChild(row);
  }
  els.drillList.appendChild(frag);
}

function renderBusinessesLevel({ db, valueColumn, valueLabel, year, occupation, page }) {
  const countRes = db.exec(
    `SELECT COUNT(*) FROM employer_occupation_year eoy
     JOIN occupations o ON o.id = eoy.occupation_id
     WHERE o.title = ? AND eoy.year = ?`,
    [occupation, year]
  );
  const total = countRes.length ? countRes[0].values[0][0] : 0;

  const dataRes = db.exec(
    `SELECT e.employer, e.address, e.fsa, e.is_noncompliant, eoy.${valueColumn}
     FROM employer_occupation_year eoy
     JOIN occupations o ON o.id = eoy.occupation_id
     JOIN lmia_employers e ON e.id = eoy.employer_id
     WHERE o.title = ? AND eoy.year = ?
     ORDER BY e.fsa ASC, eoy.${valueColumn} DESC
     LIMIT ? OFFSET ?`,
    [occupation, year, DRILL_PAGE_SIZE, page * DRILL_PAGE_SIZE]
  );
  const rows = dataRes.length ? dataRes[0].values : [];

  els.drillStatus.textContent = `${total.toLocaleString()} employers — ${occupation}, ${year}, sorted by postal code`;
  els.drillList.innerHTML = "";
  const frag = document.createDocumentFragment();
  for (const [employer, address, fsa, isNc, value] of rows) {
    const row = document.createElement("div");
    row.className = "drill-row static";
    row.innerHTML = `
      <div class="drill-row-main">
        <div class="drill-row-name"></div>
        <div class="drill-row-sub"></div>
      </div>
      <div class="drill-row-value"></div>`;
    const nameEl = row.querySelector(".drill-row-name");
    nameEl.textContent = employer;
    if (isNc) {
      const flag = document.createElement("span");
      flag.style.color = "#ef4444";
      flag.textContent = " ⚠";
      flag.title = "Also found non-compliant";
      nameEl.appendChild(flag);
    }
    row.querySelector(".drill-row-sub").textContent = `${address}${fsa ? ` (${fsa})` : ""}`;
    row.querySelector(".drill-row-value").textContent = `${value.toLocaleString()} ${valueLabel}`;
    frag.appendChild(row);
  }
  els.drillList.appendChild(frag);

  const totalPages = Math.max(1, Math.ceil(total / DRILL_PAGE_SIZE));
  els.drillPagination.hidden = total <= DRILL_PAGE_SIZE;
  els.drillPageLabel.textContent = `Page ${page + 1} of ${totalPages.toLocaleString()}`;
  els.drillPrev.disabled = page === 0;
  els.drillPrev.onclick = () => renderBusinessesLevel({ db, valueColumn, valueLabel, year, occupation, page: page - 1 });
  els.drillNext.disabled = (page + 1) * DRILL_PAGE_SIZE >= total;
  els.drillNext.onclick = () => renderBusinessesLevel({ db, valueColumn, valueLabel, year, occupation, page: page + 1 });
}

els.drillClose.addEventListener("click", closeDrillDown);
els.drillOverlay.addEventListener("click", (evt) => {
  if (evt.target === els.drillOverlay) closeDrillDown();
});
document.addEventListener("keydown", (evt) => {
  if (evt.key === "Escape" && !els.drillOverlay.hidden) closeDrillDown();
});

// Shared by both yearly occupation-stacked charts: fetches
// (year, occupation, value) from trend_occupation, cleans NOC titles,
// ranks the top N by all-time total, and builds a stacked-bar Chart.js
// config with everything outside the top N folded into "Other".
function drawYearlyTopOccupationChart(db, { canvasId, valueColumn, valueLabel, topN = 4 }) {
  const res = db.exec(
    `SELECT SUBSTR(period, 1, 4) AS year, occupation, SUM(${valueColumn}) AS v
     FROM trend_occupation GROUP BY year, occupation`
  );
  const rows = res.length ? res[0].values : [];

  const titleTotals = new Map();
  const byYear = new Map(); // year -> Map(title -> value)
  for (const [year, occupation, value] of rows) {
    const title = cleanOccupationTitle(occupation);
    titleTotals.set(title, (titleTotals.get(title) || 0) + value);
    if (!byYear.has(year)) byYear.set(year, new Map());
    const yearMap = byYear.get(year);
    yearMap.set(title, (yearMap.get(title) || 0) + value);
  }

  const top = [...titleTotals.entries()].sort((a, b) => b[1] - a[1]).slice(0, topN).map(([t]) => t);
  const topSet = new Set(top);
  const years = [...byYear.keys()].sort();

  const series = [...top, "Other"];
  const colors = [...PALETTE.slice(0, topN), "#7f7f7f"];

  const datasets = series.map((title, i) => ({
    label: title,
    backgroundColor: colors[i],
    stack: "v",
    data: years.map((year) => {
      const yearMap = byYear.get(year);
      if (title === "Other") {
        let total = 0;
        for (const [t, v] of yearMap.entries()) {
          if (!topSet.has(t)) total += v;
        }
        return total;
      }
      return yearMap.get(title) || 0;
    }),
  }));

  new Chart(document.getElementById(canvasId), {
    type: "bar",
    data: { labels: years, datasets },
    options: baseOptions({
      scales: {
        x: { stacked: true, ticks: { color: "#9aa1ad" } },
        y: { stacked: true, ticks: { color: "#9aa1ad" } },
      },
      onHover: (evt, elements, chart) => {
        evt.native.target.style.cursor = elements[0] ? "pointer" : "default";
      },
      onClick: (evt, elements, chart) => {
        const el = elements[0];
        if (!el) return;
        const label = chart.data.datasets[el.datasetIndex].label;
        const year = years[el.index];
        const ctx = { db, valueColumn, valueLabel, year };
        if (label === "Other") {
          startDrillDown(
            [{ label: year, render: null }],
            "Other",
            () => renderOccupationsLevel({ ...ctx, byYear, topSet })
          );
        } else {
          startDrillDown(
            [{ label: year, render: null }],
            label,
            () => renderBusinessesLevel({ ...ctx, occupation: label, page: 0 })
          );
        }
      },
    }),
  });
}

function drawVolumeChart(db) {
  drawYearlyTopOccupationChart(db, { canvasId: "volumeChart", valueColumn: "total_approved_positions", valueLabel: "positions", topN: 8 });
}

function drawLmiaCountByOccupationChart(db) {
  drawYearlyTopOccupationChart(db, { canvasId: "lmiaCountChart", valueColumn: "total_approved_lmias", valueLabel: "approved LMIAs", topN: 8 });
}

function drawFranchiseChart(db) {
  const res = db.exec(
    "SELECT brand, SUM(total_approved_positions) p FROM trend_franchise GROUP BY brand ORDER BY p DESC LIMIT 15"
  );
  const rows = res.length ? res[0].values : [];
  new Chart(document.getElementById("franchiseChart"), {
    type: "bar",
    data: {
      labels: rows.map((r) => r[0]),
      datasets: [{ label: "Approved positions", data: rows.map((r) => r[1]), backgroundColor: "#e4572e" }],
    },
    options: baseOptions({ indexAxis: "y", plugins: { legend: { display: false } } }),
  });
}

function drawOccupationChart(db) {
  // NOC codes changed between the 2011 and 2021 classification (e.g.
  // "6322-Cooks" vs "63200-Cooks"), which would otherwise split one
  // occupation into separate bars. Group by the title text after the
  // code instead.
  const res = db.exec(`
    SELECT
      CASE WHEN INSTR(occupation, '-') > 0
           THEN SUBSTR(occupation, INSTR(occupation, '-') + 1)
           ELSE occupation END AS title,
      SUM(total_approved_positions) p
    FROM trend_occupation
    GROUP BY title
    ORDER BY p DESC
    LIMIT 15`);
  const rows = res.length ? res[0].values : [];
  new Chart(document.getElementById("occupationChart"), {
    type: "bar",
    data: {
      labels: rows.map((r) => truncate(r[0], 45)),
      datasets: [{ label: "Approved positions", data: rows.map((r) => r[1]), backgroundColor: "#4a7ab5" }],
    },
    options: baseOptions({ indexAxis: "y", plugins: { legend: { display: false } } }),
  });
}

function drawStreamShareChart(db) {
  const res = db.exec(
    `SELECT SUBSTR(period, 1, 4) AS year, stream, SUM(total_approved_positions)
     FROM trend_stream GROUP BY year, stream ORDER BY year`
  );
  const rows = res.length ? res[0].values : [];
  const periods = [...new Set(rows.map((r) => r[0]))].sort();
  const streams = [...new Set(rows.map((r) => r[1]))].sort();
  const lookup = new Map(rows.map((r) => [`${r[0]}|${r[1]}`, r[2]]));

  const totalsByPeriod = new Map();
  for (const p of periods) {
    const total = streams.reduce((sum, s) => sum + (lookup.get(`${p}|${s}`) || 0), 0);
    totalsByPeriod.set(p, total || 1);
  }

  const datasets = streams.map((stream, i) => ({
    label: stream,
    data: periods.map((p) => (100 * (lookup.get(`${p}|${stream}`) || 0)) / totalsByPeriod.get(p)),
    backgroundColor: PALETTE[i % PALETTE.length],
    stack: "share",
  }));

  new Chart(document.getElementById("streamShareChart"), {
    type: "bar",
    data: { labels: periods, datasets },
    options: baseOptions({
      scales: {
        x: { stacked: true, ticks: { color: "#9aa1ad" } },
        y: { stacked: true, max: 100, ticks: { color: "#9aa1ad", callback: (v) => `${v}%` } },
      },
    }),
  });
}

function drawProvinceChart(db) {
  const res = db.exec(
    "SELECT province, SUM(total_approved_positions) p FROM trend_province WHERE province != '' GROUP BY province ORDER BY p DESC LIMIT 12"
  );
  const rows = res.length ? res[0].values : [];
  new Chart(document.getElementById("provinceChart"), {
    type: "bar",
    data: {
      labels: rows.map((r) => truncate(r[0], 28)),
      datasets: [{ label: "Approved positions", data: rows.map((r) => r[1]), backgroundColor: "#c9a227" }],
    },
    options: baseOptions({ indexAxis: "y", plugins: { legend: { display: false } } }),
  });
}

init().catch((err) => {
  els.loadStatus.textContent = `Could not load database: ${err.message}`;
  console.error(err);
});
