const state = {
  db: null,
  reasonCodes: {},
};

const els = {
  search: document.getElementById("search"),
  statusFilter: document.getElementById("statusFilter"),
  reasonFilter: document.getElementById("reasonFilter"),
  sort: document.getElementById("sort"),
  results: document.getElementById("results"),
  resultCount: document.getElementById("resultCount"),
  loadStatus: document.getElementById("loadStatus"),
  fetchedAt: document.getElementById("fetchedAt"),
  template: document.getElementById("row-template"),
};

const SORT_SQL = {
  "date-desc": "decision_date DESC",
  "date-asc": "decision_date ASC",
  "penalty-desc": "penalty_amount DESC",
  "name-asc": "operating_name ASC",
};

async function init() {
  const [db, reasonCodes] = await Promise.all([
    loadDatabase((received, total) => {
      if (total) {
        const mb = (received / 1e6).toFixed(1);
        const totalMb = (total / 1e6).toFixed(1);
        els.loadStatus.textContent = `Loading database… ${mb} / ${totalMb} MB`;
      }
    }),
    fetch("data/reason_codes.json").then((r) => r.json()),
  ]);
  state.db = db;
  state.reasonCodes = reasonCodes;
  els.loadStatus.hidden = true;

  const meta = db.exec("SELECT value FROM meta WHERE key='built_at'");
  if (meta.length) {
    els.fetchedAt.textContent = new Date(meta[0].values[0][0]).toLocaleDateString("en-CA");
  }

  populateReasonFilter();
  render();

  els.search.addEventListener("input", render);
  els.statusFilter.addEventListener("change", render);
  els.reasonFilter.addEventListener("change", render);
  els.sort.addEventListener("change", render);
}

function populateReasonFilter() {
  const entries = Object.entries(state.reasonCodes).sort((a, b) => Number(a[0]) - Number(b[0]));
  for (const [code, text] of entries) {
    const opt = document.createElement("option");
    opt.value = code;
    opt.textContent = `${code}. ${text}`;
    els.reasonFilter.appendChild(opt);
  }
}

function buildQuery() {
  const query = els.search.value.trim();
  const statusWanted = els.statusFilter.value;
  const reasonWanted = els.reasonFilter.value;
  const sortMode = els.sort.value;

  const clauses = [];
  const params = [];
  const fromClause = "non_compliant n";

  if (query) {
    const pattern = likePattern(query);
    clauses.push("(n.operating_name LIKE ? ESCAPE '\\' OR n.legal_name LIKE ? ESCAPE '\\' OR n.address LIKE ? ESCAPE '\\')");
    params.push(pattern, pattern, pattern);
  }
  if (statusWanted) {
    clauses.push("n.status LIKE ?");
    params.push(`${statusWanted}%`);
  }
  if (reasonWanted) {
    clauses.push("(',' || n.reason_codes || ',') LIKE ?");
    params.push(`%,${reasonWanted},%`);
  }

  const where = clauses.length ? `WHERE ${clauses.join(" AND ")}` : "";
  const orderBy = SORT_SQL[sortMode] || SORT_SQL["date-desc"];

  const sql = `
    SELECT n.operating_name, n.legal_name, n.address, n.reasons,
           n.decision_date, n.penalty, n.status,
           CAST(REPLACE(REPLACE(n.penalty, '$', ''), ',', '') AS INTEGER) AS penalty_amount
    FROM ${fromClause}
    ${where}
    ORDER BY ${orderBy}
    LIMIT 300`;

  const countSql = `SELECT COUNT(*) FROM ${fromClause} ${where}`;

  return { sql, countSql, params };
}

function render() {
  const { sql, countSql, params } = buildQuery();

  const countRes = state.db.exec(countSql, params);
  const total = countRes.length ? countRes[0].values[0][0] : 0;

  const dataRes = state.db.exec(sql, params);
  const rows = dataRes.length ? dataRes[0].values : [];

  const shown = Math.min(rows.length, 300);
  els.resultCount.textContent =
    total > shown
      ? `${total.toLocaleString()} employers match (showing first ${shown})`
      : `${total.toLocaleString()} employers match`;

  els.results.innerHTML = "";
  const frag = document.createDocumentFragment();
  for (const row of rows) {
    frag.appendChild(renderCard(row));
  }
  els.results.appendChild(frag);
}

function renderCard(row) {
  const [operatingName, legalName, address, reasonsText, decisionDate, penalty, status] = row;

  const node = els.template.content.cloneNode(true);
  node.querySelector(".card-name").textContent = operatingName;

  const legalEl = node.querySelector(".card-legal");
  if (legalName && legalName !== operatingName) {
    legalEl.textContent = `Legal name: ${legalName}`;
  } else {
    legalEl.remove();
  }

  node.querySelector(".card-address").textContent = address;

  const reasonsEl = node.querySelector(".card-reasons");
  for (const reason of reasonsText.split("; ").filter(Boolean)) {
    const li = document.createElement("li");
    li.textContent = reason;
    reasonsEl.appendChild(li);
  }

  node.querySelector(".card-date").textContent = `Decision: ${decisionDate}`;
  node.querySelector(".card-penalty").textContent = `Penalty: ${penalty}`;

  const statusEl = node.querySelector(".card-status");
  statusEl.textContent = status;
  if (status.startsWith("Ineligible")) {
    statusEl.classList.add("status-ineligible");
  }

  return node;
}

init().catch((err) => {
  els.loadStatus.textContent = `Could not load database: ${err.message}`;
  console.error(err);
});
