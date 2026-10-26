const PAGE_SIZE = 50;

const state = {
  db: null,
  page: 0,
  totalRows: 0,
};

const els = {
  search: document.getElementById("search"),
  provinceFilter: document.getElementById("provinceFilter"),
  flagFilter: document.getElementById("flagFilter"),
  sort: document.getElementById("sort"),
  results: document.getElementById("results"),
  resultCount: document.getElementById("resultCount"),
  loadStatus: document.getElementById("loadStatus"),
  fetchedAt: document.getElementById("fetchedAt"),
  template: document.getElementById("row-template"),
  pagination: document.getElementById("pagination"),
  prevPage: document.getElementById("prevPage"),
  nextPage: document.getElementById("nextPage"),
  pageLabel: document.getElementById("pageLabel"),
};

const SORT_SQL = {
  recent: "last_period DESC, employer ASC",
  positions: "total_approved_positions DESC",
  lmias: "total_approved_lmias DESC",
  name: "employer ASC",
};

function debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

async function init() {
  els.loadStatus.textContent = "Loading database…";
  const db = await loadDatabase((received, total) => {
    if (total) {
      const mb = (received / 1e6).toFixed(1);
      const totalMb = (total / 1e6).toFixed(1);
      els.loadStatus.textContent = `Loading database… ${mb} / ${totalMb} MB`;
    }
  });
  state.db = db;
  els.loadStatus.hidden = true;

  const meta = db.exec("SELECT value FROM meta WHERE key='built_at'");
  if (meta.length) {
    els.fetchedAt.textContent = new Date(meta[0].values[0][0]).toLocaleDateString("en-CA");
  }

  populateProvinces();
  runQuery();

  const debouncedQuery = debounce(() => {
    state.page = 0;
    runQuery();
  }, 250);

  els.search.addEventListener("input", debouncedQuery);
  els.provinceFilter.addEventListener("change", () => {
    state.page = 0;
    runQuery();
  });
  els.flagFilter.addEventListener("change", () => {
    state.page = 0;
    runQuery();
  });
  els.sort.addEventListener("change", () => {
    state.page = 0;
    runQuery();
  });
  els.prevPage.addEventListener("click", () => {
    if (state.page > 0) {
      state.page -= 1;
      runQuery();
    }
  });
  els.nextPage.addEventListener("click", () => {
    if ((state.page + 1) * PAGE_SIZE < state.totalRows) {
      state.page += 1;
      runQuery();
    }
  });
}

function populateProvinces() {
  const res = state.db.exec(
    "SELECT DISTINCT province FROM lmia_employers WHERE province != '' ORDER BY province"
  );
  if (!res.length) return;
  for (const [province] of res[0].values) {
    const opt = document.createElement("option");
    opt.value = province;
    opt.textContent = province;
    els.provinceFilter.appendChild(opt);
  }
}

function buildWhere() {
  const clauses = [];
  const params = [];
  const query = els.search.value.trim();

  const fromClause = "lmia_employers e";
  if (query) {
    const pattern = likePattern(query);
    clauses.push("(e.employer LIKE ? ESCAPE '\\' OR e.address LIKE ? ESCAPE '\\')");
    params.push(pattern, pattern);
  }

  if (els.provinceFilter.value) {
    clauses.push("e.province = ?");
    params.push(els.provinceFilter.value);
  }
  if (els.flagFilter.value) {
    clauses.push("e.is_noncompliant = 1");
  }

  const where = clauses.length ? `WHERE ${clauses.join(" AND ")}` : "";
  return { fromClause, where, params };
}

function runQuery() {
  const { fromClause, where, params } = buildWhere();
  const orderBy = SORT_SQL[els.sort.value] || SORT_SQL.recent;

  const countSql = `SELECT COUNT(*) FROM ${fromClause} ${where}`;
  const countRes = state.db.exec(countSql, params);
  state.totalRows = countRes.length ? countRes[0].values[0][0] : 0;

  const dataSql = `
    SELECT e.employer, e.address, e.province, e.streams, e.total_approved_lmias,
           e.total_approved_positions, e.first_period, e.last_period,
           e.periods_seen, e.is_noncompliant
    FROM ${fromClause} ${where}
    ORDER BY ${orderBy}
    LIMIT ? OFFSET ?`;
  const dataRes = state.db.exec(dataSql, [...params, PAGE_SIZE, state.page * PAGE_SIZE]);

  render(dataRes.length ? dataRes[0].values : []);
}

function render(rows) {
  els.resultCount.textContent = `${state.totalRows.toLocaleString()} employers match`;
  els.results.innerHTML = "";

  const frag = document.createDocumentFragment();
  for (const row of rows) {
    frag.appendChild(renderCard(row));
  }
  els.results.appendChild(frag);

  const totalPages = Math.max(1, Math.ceil(state.totalRows / PAGE_SIZE));
  els.pagination.hidden = state.totalRows <= PAGE_SIZE;
  els.pageLabel.textContent = `Page ${state.page + 1} of ${totalPages.toLocaleString()}`;
  els.prevPage.disabled = state.page === 0;
  els.nextPage.disabled = (state.page + 1) * PAGE_SIZE >= state.totalRows;
}

// Provincial/territorial numbered-company suffixes mapped to their
// free official corporate-registry search tool. "Canada" gets the
// federal search; anything else we don't recognize falls back to a
// plain web search so the link still works without us guessing at
// (and possibly breaking) every jurisdiction's URL.
const REGISTRY_LINKS = {
  CANADA: "https://ised-isde.canada.ca/cc/lgcy/fdrlCrpSrch.html?locale=en_CA",
  ONTARIO: "https://www.ontario.ca/page/ontario-business-registry",
  "BRITISH COLUMBIA": "https://www.bcregistry.gov.bc.ca/",
  BC: "https://www.bcregistry.gov.bc.ca/",
  MANITOBA: "https://companiesoffice.gov.mb.ca/",
  "NOVA SCOTIA": "https://rjsc.novascotia.ca/",
};
const NUMBERED_COMPANY_RE = /^(\d{6,9})\s+([A-Za-z.& ]+?)\s+(Inc\.?|Ltd\.?|Limited|Corp\.?|Corporation)\b/i;

function registryLinkFor(employer) {
  const m = employer.match(NUMBERED_COMPANY_RE);
  if (!m) return null;
  const [, number, jurisdiction] = m;
  const key = jurisdiction.trim().toUpperCase();
  const url = REGISTRY_LINKS[key];
  if (url) {
    return { url, label: `Look up #${number} on the official ${jurisdiction.trim()} registry →` };
  }
  const query = encodeURIComponent(`"${number}" "${jurisdiction.trim()}" corporate registry`);
  return {
    url: `https://www.google.com/search?q=${query}`,
    label: `Search for #${number} (${jurisdiction.trim()}) on a public registry →`,
  };
}

function renderCard(row) {
  const [
    employer, address, province, streams, totalLmias,
    totalPositions, firstPeriod, lastPeriod, periodsSeen, isNoncompliant,
  ] = row;

  const node = els.template.content.cloneNode(true);
  node.querySelector(".card-name").textContent = employer;
  node.querySelector(".card-address").textContent = address;

  const flagEl = node.querySelector(".card-flag");
  if (isNoncompliant) {
    flagEl.hidden = false;
  } else {
    flagEl.remove();
  }

  const registryEl = node.querySelector(".card-registry");
  const registry = registryLinkFor(employer);
  if (registry) {
    const a = document.createElement("a");
    a.href = registry.url;
    a.target = "_blank";
    a.rel = "noopener";
    a.textContent = registry.label;
    registryEl.appendChild(a);
    registryEl.hidden = false;
  } else {
    registryEl.remove();
  }

  node.querySelector(".card-province").textContent = province;
  node.querySelector(".card-streams").textContent = streams || "—";
  node.querySelector(".card-lmias").textContent = `${totalLmias.toLocaleString()} approved LMIA${totalLmias === 1 ? "" : "s"}`;
  node.querySelector(".card-positions").textContent = `${totalPositions.toLocaleString()} position${totalPositions === 1 ? "" : "s"}`;

  const periodText = firstPeriod === lastPeriod
    ? firstPeriod
    : `${firstPeriod} – ${lastPeriod} (${periodsSeen} periods)`;
  node.querySelector(".card-period").textContent = periodText;

  return node;
}

init().catch((err) => {
  els.loadStatus.textContent = `Could not load database: ${err.message}`;
  console.error(err);
});
