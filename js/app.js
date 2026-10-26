const state = {
  employers: [],
  reasonCodes: {},
  fetchedAt: "",
};

const els = {
  search: document.getElementById("search"),
  statusFilter: document.getElementById("statusFilter"),
  reasonFilter: document.getElementById("reasonFilter"),
  sort: document.getElementById("sort"),
  results: document.getElementById("results"),
  resultCount: document.getElementById("resultCount"),
  fetchedAt: document.getElementById("fetchedAt"),
  template: document.getElementById("row-template"),
};

async function init() {
  const [employersRes, reasonsRes] = await Promise.all([
    fetch("data/non_compliant_employers.json"),
    fetch("data/reason_codes.json"),
  ]);
  const payload = await employersRes.json();
  state.employers = payload.employers;
  state.fetchedAt = payload.fetched_at;
  state.reasonCodes = await reasonsRes.json();

  els.fetchedAt.textContent = new Date(state.fetchedAt).toLocaleDateString("en-CA");
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

function render() {
  const query = els.search.value.trim().toLowerCase();
  const statusWanted = els.statusFilter.value;
  const reasonWanted = els.reasonFilter.value;
  const sortMode = els.sort.value;

  let filtered = state.employers.filter((e) => {
    if (statusWanted && !e.status.startsWith(statusWanted)) return false;
    if (reasonWanted && !e.reason_codes.includes(Number(reasonWanted))) return false;
    if (query) {
      const haystack = `${e.operating_name} ${e.legal_name} ${e.address}`.toLowerCase();
      if (!haystack.includes(query)) return false;
    }
    return true;
  });

  filtered = sortResults(filtered, sortMode);

  els.resultCount.textContent = `${filtered.length.toLocaleString()} of ${state.employers.length.toLocaleString()} employers shown`;

  els.results.innerHTML = "";
  const frag = document.createDocumentFragment();
  for (const e of filtered.slice(0, 300)) {
    frag.appendChild(renderCard(e));
  }
  els.results.appendChild(frag);
}

function parsePenaltyAmount(penaltyText) {
  const match = penaltyText.replace(/,/g, "").match(/\$(\d+)/);
  return match ? Number(match[1]) : 0;
}

function sortResults(list, mode) {
  const copy = [...list];
  switch (mode) {
    case "date-asc":
      return copy.sort((a, b) => a.decision_date.localeCompare(b.decision_date));
    case "penalty-desc":
      return copy.sort((a, b) => parsePenaltyAmount(b.penalty) - parsePenaltyAmount(a.penalty));
    case "name-asc":
      return copy.sort((a, b) => a.operating_name.localeCompare(b.operating_name));
    case "date-desc":
    default:
      return copy.sort((a, b) => b.decision_date.localeCompare(a.decision_date));
  }
}

function renderCard(e) {
  const node = els.template.content.cloneNode(true);
  node.querySelector(".card-name").textContent = e.operating_name;

  const legalEl = node.querySelector(".card-legal");
  if (e.legal_name && e.legal_name !== e.operating_name) {
    legalEl.textContent = `Legal name: ${e.legal_name}`;
  } else {
    legalEl.remove();
  }

  node.querySelector(".card-address").textContent = e.address;

  const reasonsEl = node.querySelector(".card-reasons");
  for (const reason of e.reasons) {
    const li = document.createElement("li");
    li.textContent = reason;
    reasonsEl.appendChild(li);
  }

  node.querySelector(".card-date").textContent = `Decision: ${e.decision_date}`;
  node.querySelector(".card-penalty").textContent = `Penalty: ${e.penalty}`;

  const statusEl = node.querySelector(".card-status");
  statusEl.textContent = e.status;
  if (e.status.startsWith("Ineligible")) {
    statusEl.classList.add("status-ineligible");
  }

  return node;
}

init().catch((err) => {
  els.results.innerHTML = `<p>Could not load data: ${err.message}</p>`;
});
