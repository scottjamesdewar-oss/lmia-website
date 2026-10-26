const PALETTE = [
  "#e4572e", "#4c9f70", "#4a7ab5", "#c9a227", "#9b5de5",
  "#2fbf71", "#e85d75", "#5aa9e6", "#f4a261", "#7f7f7f",
];

const els = {
  loadStatus: document.getElementById("loadStatus"),
  chartsRoot: document.getElementById("chartsRoot"),
};

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

// Shared by both yearly occupation-stacked charts: fetches
// (year, occupation, value) from trend_occupation, cleans NOC titles,
// ranks the top N by all-time total, and builds a stacked-bar Chart.js
// config with everything outside the top N folded into "Other".
function drawYearlyTopOccupationChart(db, { canvasId, valueColumn, topN = 4 }) {
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
    }),
  });
}

function drawVolumeChart(db) {
  drawYearlyTopOccupationChart(db, { canvasId: "volumeChart", valueColumn: "total_approved_positions", topN: 8 });
}

function drawLmiaCountByOccupationChart(db) {
  drawYearlyTopOccupationChart(db, { canvasId: "lmiaCountChart", valueColumn: "total_approved_lmias", topN: 8 });
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
