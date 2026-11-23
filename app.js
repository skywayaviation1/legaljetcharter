const SUFFIXES = new Set(["INC", "INCORPORATED", "LLC", "LLP", "LP", "CO", "COMPANY", "LTD", "LIMITED", "CORP", "CORPORATION", "PLLC"]);

const state = {
  operators: [],
  meta: null,
  watch: [],
  watchIndex: new Map(),
  shown: 40,
  openId: null,
};

const $ = (id) => document.getElementById(id);

function valueOf(field) {
  if (!field || typeof field !== "object" || !("value" in field)) return field || "";
  return field.value;
}

function sourceOf(field) {
  if (!field || typeof field !== "object") return "";
  if (field.source === "both") return "Part 135 list and Air Operator search";
  if (field.source === "part135-list") return "Part 135 list";
  if (field.source === "air-operator") return "Air Operator search";
  if (field.source === "site") return field.note || "Added on this site. Not in the FAA lists.";
  return field.source || "";
}

function normName(input) {
  const tokens = String(input || "")
    .toUpperCase()
    .replace(/&/g, " AND ")
    .replace(/[^A-Z0-9]+/g, " ")
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  while (tokens.length && SUFFIXES.has(tokens[tokens.length - 1])) tokens.pop();
  return tokens.join(" ");
}

function formatDay(isoDate) {
  if (!isoDate) return "";
  const iso = isoDate.length === 10 ? `${isoDate}T00:00:00Z` : isoDate;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return isoDate;
  return new Intl.DateTimeFormat("en-US", {
    month: "long", day: "numeric", year: "numeric", timeZone: "UTC",
  }).format(date);
}

function haystack(op) {
  const bits = [
    valueOf(op.legalName),
    valueOf(op.airOperatorName),
    valueOf(op.designator),
    valueOf(op.certificateNumber),
    valueOf(op.city),
    valueOf(op.state),
    ...(valueOf(op.dbas) || []),
    ...(op.aircraft || []).map((craft) => craft.registration),
  ];
  return bits.filter(Boolean).join(" ").toLowerCase();
}

function namesForWatch(op) {
  return [valueOf(op.legalName), valueOf(op.airOperatorName), ...(valueOf(op.dbas) || [])]
    .map(normName)
    .filter(Boolean);
}

function rank(op, query) {
  const q = query.toLowerCase().replace(/\s+/g, "");
  const name = String(valueOf(op.legalName) || "").toLowerCase();
  const designator = String(valueOf(op.designator) || "").toLowerCase();
  const certificate = String(valueOf(op.certificateNumber) || "").toLowerCase().replace(/\s+/g, "");
  const regs = (op.aircraft || []).map((craft) => String(craft.registration || "").toLowerCase().replace(/\s+/g, ""));
  if (designator && designator === q) return 0;
  if (certificate && certificate === q) return 0;
  if (regs.includes(q)) return 0;
  if (name.startsWith(query.toLowerCase())) return 1;
  return 2;
}

function passes(op, query) {
  const presence = $("filter-presence").value;
  const stateCode = $("filter-state").value;
  const conflicts = $("filter-conflict").checked;
  if (presence && op.presence !== presence) return false;
  if (stateCode && valueOf(op.state) !== stateCode) return false;
  if (conflicts && !(op.conflicts || []).length) return false;
  if (!query) return true;
  return op._hay.includes(query.toLowerCase());
}

function currentQuery() {
  return $("q").value.trim();
}

function filtered() {
  const query = currentQuery();
  const rows = state.operators.filter((op) => passes(op, query));
  const sort = $("sort").value;
  rows.sort((a, b) => {
    if (query && sort === "relevance") {
      const delta = rank(a, query) - rank(b, query);
      if (delta) return delta;
    }
    if (sort === "aircraft") return (b.aircraftCount || 0) - (a.aircraftCount || 0) || nameOf(a).localeCompare(nameOf(b));
    if (sort === "state") return String(valueOf(a.state)).localeCompare(String(valueOf(b.state))) || nameOf(a).localeCompare(nameOf(b));
    return nameOf(a).localeCompare(nameOf(b));
  });
  return rows;
}

function nameOf(op) {
  return String(valueOf(op.legalName) || "");
}

function badge(op) {
  if (op.presence === "both") return ["both", "On both FAA sources"];
  if (op.presence === "part135-list") return ["only", "Part 135 list only"];
  return ["only", "Air Operator search only"];
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function addField(table, label, field) {
  const raw = valueOf(field);
  const empty = raw == null || raw === "" || (Array.isArray(raw) && raw.length === 0);
  if (empty) return;
  const row = document.createElement("tr");
  row.appendChild(el("th", "", label));
  const cell = document.createElement("td");
  cell.append(document.createTextNode(Array.isArray(raw) ? raw.join(", ") : String(raw)));
  const source = sourceOf(field);
  if (source) {
    cell.appendChild(document.createElement("br"));
    cell.appendChild(el("span", "source", source));
  }
  row.appendChild(cell);
  table.appendChild(row);
}

function renderDetail(op) {
  const box = el("div", "detail");
  const notes = state.watchIndex.get(op.id) || [];
  for (const note of notes) {
    const block = el("div", "watch");
    block.appendChild(el("strong", "", "Editorial note, not an FAA finding. "));
    block.append(document.createTextNode(note.summary || "Watchlist entry"));
    if (note.details) {
      const p = el("p", "");
      p.textContent = note.details;
      block.appendChild(p);
    }
    box.appendChild(block);
  }
  if ((op.conflicts || []).length) {
    const block = el("div", "conflict");
    block.appendChild(el("strong", "", "The two FAA sources do not print the same value. "));
    const list = document.createElement("ul");
    for (const item of op.conflicts) {
      const li = document.createElement("li");
      li.textContent = `${item.field}: Part 135 list “${item.part135List || "—"}”; Air Operator search “${item.airOperator || "—"}”.`;
      list.appendChild(li);
    }
    block.appendChild(list);
    box.appendChild(block);
  }
  const table = document.createElement("table");
  addField(table, "Legal name", op.legalName);
  addField(table, "Name on Air Operator search", op.airOperatorName);
  addField(table, "DBA", op.dbas);
  addField(table, "Designator", op.designator);
  addField(table, "Certificate number", op.certificateNumber);
  addField(table, "Issue date", op.issueDate);
  addField(table, "City", op.city);
  addField(table, "State", op.state);
  addField(table, "Postal code", op.postalCode);
  addField(table, "Address", op.address);
  addField(table, "FSDO / district office", op.fsdo);
  addField(table, "Status", op.status);
  addField(table, "Chief executive", op.ceo);
  addField(table, "Chief pilot", op.chiefPilot);
  addField(table, "Director of maintenance", op.directorOfMaintenance);
  addField(table, "Director of operations", op.directorOfOperations);
  addField(table, "Phone", op.phone);
  addField(table, "Country", op.country);
  const staff = valueOf(op.staff);
  if (staff && typeof staff === "object") {
    const bits = Object.entries(staff).filter(([, val]) => val !== "" && val != null);
    if (bits.length) addField(table, "Staffing counts", { value: bits.map(([key, val]) => `${key} ${val}`).join(", "), source: sourceOf(op.staff) });
  }
  addField(table, "Authority on the Part 135 list", op.cfrOnList);
  addField(table, "Part on Air Operator search", op.cfrOnSearch);
  if (op.extra && Object.keys(op.extra).length) {
    for (const [key, val] of Object.entries(op.extra)) {
      addField(table, key, { value: val, source: "air-operator" });
    }
  }
  box.appendChild(table);

  const craft = op.aircraft || [];
  if (craft.length) {
    box.appendChild(el("h4", "", `Aircraft on the Part 135 list (${craft.length})`));
    const grid = document.createElement("table");
    grid.className = "aircraft";
    const head = document.createElement("thead");
    const headRow = document.createElement("tr");
    for (const label of ["N-number", "Model", "Serial", "CFR"]) headRow.appendChild(el("th", "", label));
    head.appendChild(headRow);
    grid.appendChild(head);
    const body = document.createElement("tbody");
    const sorted = craft.slice().sort((a, b) => String(a.registration).localeCompare(String(b.registration)));
    const visible = state.openId === op.id + ":all" ? sorted : sorted.slice(0, 12);
    for (const row of visible) {
      const tr = document.createElement("tr");
      for (const key of ["registration", "model", "serial", "cfr"]) {
        tr.appendChild(el("td", "", row[key] || ""));
      }
      body.appendChild(tr);
    }
    grid.appendChild(body);
    box.appendChild(grid);
    if (sorted.length > 12 && state.openId !== op.id + ":all") {
      const more = el("button", "linkish", `Show all ${sorted.length} aircraft`);
      more.type = "button";
      more.addEventListener("click", () => {
        state.openId = op.id + ":all";
        render();
      });
      box.appendChild(more);
    }
  }
  const searchCraft = op.searchAircraft || [];
  if (searchCraft.length) {
    box.appendChild(el("h4", "", "Aircraft noted on Air Operator search"));
    const list = document.createElement("ul");
    for (const item of searchCraft) {
      list.appendChild(el("li", "", [item.registration, item.model].filter(Boolean).join(" · ") || "Listed"));
    }
    box.appendChild(list);
  }
  box.appendChild(renderSafety(op));
  return box;
}

function formatIso(value) {
  if (!value) return "";
  const date = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en-US", { month: "long", day: "numeric", year: "numeric", timeZone: "UTC" }).format(date);
}

function renderSafety(op) {
  const section = el("section", "safety");
  section.appendChild(el("h4", "", "Safety history"));
  const note = el("p", "safety-note");
  note.textContent = "These are public records matched from the sources below. Finding none in a source is not a clean-record guarantee, and a listing is not a rating of current fitness to fly.";
  section.appendChild(note);
  const safety = op.safety || { ntsb: [], enforcement: [] };
  const meta = (state.meta && state.meta.safety) || {};
  const ntsbMeta = meta.ntsb || {};
  const enfMeta = meta.enforcement || {};
  section.appendChild(el("h5", "", "NTSB accidents and incidents"));
  const ntsb = safety.ntsb || [];
  if (!ntsb.length) {
    const fileDate = formatIso(ntsbMeta.fileDate) || "the current file date";
    const through = formatIso(ntsbMeta.updatesThrough);
    const updates = through && through !== fileDate ? ` plus weekly updates through ${through}` : "";
    section.appendChild(el("p", "", `No NTSB accident or incident matched this operator in avall.zip (file dated ${fileDate}${updates}) or in NTSB investigation pages for events on or after that file date.`));
  } else {
    const list = document.createElement("ul");
    for (const item of ntsb) {
      const li = document.createElement("li");
      const place = [item.city, item.state].filter(Boolean).join(", ");
      const craft = (item.aircraft || []).map((row) => [row.registration, row.make, row.model].filter(Boolean).join(" ")).filter(Boolean).join("; ");
      li.append(`${formatIso(item.date) || "Date not in the file"} · ${item.eventType || "Record"}`);
      if (place) li.append(` · ${place}`);
      if (item.ntsbNumber) li.append(` · ${item.ntsbNumber}`);
      if (craft) li.append(` · ${craft}`);
      if (item.highestInjury) li.append(` · highest injury ${item.highestInjury}`);
      if (item.farPart) li.append(` · FAR part ${item.farPart}`);
      else if (item.preliminary) li.append(" · FAR part not coded on the investigation page");
      if (item.preliminary) li.append(" · preliminary");
      li.append(". ");
      if (item.url) {
        const link = document.createElement("a");
        link.href = item.url;
        link.textContent = item.preliminary ? "NTSB investigation" : "NTSB report";
        link.rel = "noopener";
        li.appendChild(link);
      }
      li.append(` · ${item.match || "match not labeled"}`);
      if (item.sourceOperator) li.append(` · source name “${item.sourceOperator}”`);
      list.appendChild(li);
    }
    section.appendChild(list);
  }
  section.appendChild(el("h5", "", "FAA enforcement reports"));
  const actions = safety.enforcement || [];
  if (!actions.length) {
    const start = formatIso(enfMeta.coverageStart);
    const end = formatIso(enfMeta.coverageEnd);
    const span = start && end ? ` covering ${start} through ${end}` : "";
    section.appendChild(el("p", "", `No closed civil penalty or certificate action naming this operator was found in the FAA quarterly enforcement spreadsheets linked from the FAA enforcement reports page${span}.`));
  } else {
    const list = document.createElement("ul");
    for (const item of actions) {
      const li = document.createElement("li");
      const money = item.amount ? ` · ${item.amount} ${item.sanction || ""}`.trimEnd() : "";
      li.append(`${formatIso(item.closedDate) || "Closed date not in the file"} · ${item.action || "Enforcement action"} · ${item.name || ""}`);
      if (item.caseNumber) li.append(` · case ${item.caseNumber}`);
      if (item.caseType) li.append(` · ${item.caseType}`);
      if (money) li.append(money);
      li.append(". ");
      if (item.url) {
        const link = document.createElement("a");
        link.href = item.url;
        link.textContent = item.quarter ? `FAA report ${item.quarter}` : "FAA report";
        link.rel = "noopener";
        li.appendChild(link);
      }
      li.append(` · ${item.match || "name match - verify"}`);
      list.appendChild(li);
    }
    section.appendChild(list);
  }
  const sources = el("p", "safety-note");
  const ntsbLink = document.createElement("a");
  ntsbLink.href = ntsbMeta.sourceUrl || "https://data.ntsb.gov/avdata/";
  ntsbLink.textContent = "NTSB aviation accident database";
  const faaLink = document.createElement("a");
  faaLink.href = enfMeta.sourceUrl || "https://www.faa.gov/about/office_org/headquarters_offices/agc/practice_areas/enforcement/reports";
  faaLink.textContent = "FAA quarterly enforcement reports";
  sources.appendChild(ntsbLink);
  sources.append(" · ");
  sources.appendChild(faaLink);
  section.appendChild(sources);
  return section;
}

function renderCard(op) {
  const card = el("article", "card");
  card.id = "op-" + op.id.replace(/[^a-z0-9:-]+/gi, "");
  const head = el("div", "card-head");
  const text = el("div");
  text.appendChild(el("h3", "", nameOf(op) || "Unnamed operator"));
  const place = [valueOf(op.city), valueOf(op.state)].filter(Boolean).join(", ");
  const bits = [
    valueOf(op.designator) ? `Designator ${valueOf(op.designator)}` : "",
    valueOf(op.certificateNumber) ? `Certificate ${valueOf(op.certificateNumber)}` : "",
    place,
  ].filter(Boolean);
  text.appendChild(el("p", "meta", bits.join(" · ") || "No location published in either FAA source"));
  const badges = el("div", "badges");
  const [kind, label] = badge(op);
  badges.appendChild(el("span", `badge ${kind}`, label));
  if ((op.conflicts || []).length) badges.appendChild(el("span", "badge diff", "Fields differ"));
  const safety = op.safety || {};
  const safetyCount = (safety.ntsb || []).length + (safety.enforcement || []).length;
  if (safetyCount) badges.appendChild(el("span", "badge note", `${safetyCount} safety record${safetyCount === 1 ? "" : "s"}`));
  if ((state.watchIndex.get(op.id) || []).length) badges.appendChild(el("span", "badge note", "Editorial note"));
  text.appendChild(badges);
  head.appendChild(text);
  const side = el("div");
  side.appendChild(el("div", "count-pill", `${op.aircraftCount || 0} aircraft`));
  const open = state.openId === op.id || state.openId === `${op.id}:all`;
  const toggle = el("button", "linkish", open ? "Hide details" : "Details");
  toggle.type = "button";
  toggle.setAttribute("aria-expanded", open ? "true" : "false");
  toggle.addEventListener("click", () => {
    state.openId = open ? null : op.id;
    render();
  });
  side.appendChild(toggle);
  head.appendChild(side);
  card.appendChild(head);
  if (state.openId === op.id || state.openId === `${op.id}:all`) card.appendChild(renderDetail(op));
  return card;
}

function render() {
  const query = currentQuery();
  const rows = filtered();
  const browsing = query.length < 2 && !$("filter-state").value && !$("filter-presence").value && !$("filter-conflict").checked;
  const host = $("results");
  host.replaceChildren();
  if (browsing) {
    host.appendChild(el("p", "empty", "Type at least two characters, or choose a state or source to browse. Nothing is hidden: filters only narrow this FAA copy."));
    $("result-count").textContent = `${state.operators.length.toLocaleString()} operators loaded.`;
    return;
  }
  $("result-count").textContent = `${rows.length.toLocaleString()} operator${rows.length === 1 ? "" : "s"}`;
  if (!rows.length) {
    host.appendChild(el("p", "empty", "No operator in the loaded FAA files matches that search. The FAA says to contact the local FSDO before treating a missing name as either legal or illegal."));
    return;
  }
  const slice = rows.slice(0, state.shown);
  for (const op of slice) host.appendChild(renderCard(op));
  if (rows.length > slice.length) {
    const more = el("button", "more", `Show more (${rows.length - slice.length} left)`);
    more.type = "button";
    more.addEventListener("click", () => {
      state.shown += 40;
      render();
    });
    host.appendChild(more);
  }
}

function fillStates() {
  const states = [...new Set(state.operators.map((op) => valueOf(op.state)).filter(Boolean))].sort();
  const select = $("filter-state");
  for (const code of states) {
    const option = document.createElement("option");
    option.value = code;
    option.textContent = code;
    select.appendChild(option);
  }
}

function renderBanner() {
  const meta = state.meta || {};
  const fileDate = formatDay(meta.part135 && meta.part135.fileDate);
  const checked = formatDay(meta.lastChecked);
  const airLabel = (meta.airOperator && meta.airOperator.updatedLabel) || "";
  const line = $("data-line");
  line.replaceChildren();
  line.append("Data from FAA, updated ");
  line.appendChild(el("strong", "", fileDate || "date not on the FAA file"));
  if (airLabel && !airLabel.includes(fileDate)) {
    line.append(` (Air Operator search: ${airLabel})`);
  }
  line.append("; last checked ");
  line.appendChild(el("strong", "", checked || "—"));
  line.append(". ");
  const list = document.createElement("a");
  list.href = meta.holdersPage || "https://www.faa.gov/about/officeorg/headquartersoffices/avs/faa-certificated-aircraft-operators-legal-part-135-holders";
  list.textContent = "Part 135 list";
  const search = document.createElement("a");
  search.href = meta.airOperatorPage || "https://www.faa.gov/av-info/air-operator-far-search";
  search.textContent = "Air Operator search";
  line.appendChild(list);
  line.append(" · ");
  line.appendChild(search);
  const recon = meta.reconciliation || {};
  if (recon.inBoth != null) {
    line.append(` · ${recon.inBoth.toLocaleString()} on both sources, ${recon.onlyPart135List.toLocaleString()} only on the Part 135 list, ${recon.onlyAirOperator.toLocaleString()} only on Air Operator search`);
    if (recon.conflictCount) line.append(`, ${recon.conflictCount.toLocaleString()} differing field${recon.conflictCount === 1 ? "" : "s"}`);
    line.append(".");
  }
  $("stat-ops").textContent = (meta.operatorCount || state.operators.length || 0).toLocaleString();
  $("stat-aircraft").textContent = (meta.aircraftCount || 0).toLocaleString();
  $("stat-both").textContent = recon.inBoth != null ? recon.inBoth.toLocaleString() : "—";
}

function indexWatchlist() {
  const byName = new Map();
  for (const note of state.watch) {
    const key = normName(note.operatorName);
    if (!key) continue;
    if (!byName.has(key)) byName.set(key, []);
    byName.get(key).push(note);
  }
  for (const op of state.operators) {
    const hits = [];
    for (const name of namesForWatch(op)) {
      for (const note of byName.get(name) || []) {
        if (!hits.includes(note)) hits.push(note);
      }
    }
    if (hits.length) state.watchIndex.set(op.id, hits);
  }
}

function readQuery() {
  const params = new URLSearchParams(location.search);
  if (params.get("q")) $("q").value = params.get("q");
}

function bind() {
  const resetShown = () => { state.shown = 40; render(); };
  $("search-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const url = new URL(location.href);
    const query = currentQuery();
    if (query) url.searchParams.set("q", query);
    else url.searchParams.delete("q");
    history.replaceState(null, "", url);
    resetShown();
  });
  $("q").addEventListener("input", resetShown);
  for (const id of ["filter-state", "filter-presence", "sort", "filter-conflict"]) {
    $(id).addEventListener("change", resetShown);
  }
  document.addEventListener("keydown", (event) => {
    if (event.key === "/" && document.activeElement !== $("q")) {
      event.preventDefault();
      $("q").focus();
    }
  });
}

async function load() {
  const [operators, meta, watch] = await Promise.all([
    fetch("data/operators.json").then((res) => {
      if (!res.ok) throw new Error("operators");
      return res.json();
    }),
    fetch("data/meta.json").then((res) => res.json()),
    fetch("data/watchlist.json").then((res) => res.json()).catch(() => []),
  ]);
  state.operators = operators.map((op) => ({ ...op, _hay: haystack(op) }));
  state.meta = meta;
  state.watch = Array.isArray(watch) ? watch : [];
  indexWatchlist();
  fillStates();
  renderBanner();
  readQuery();
  bind();
  render();
}

load().catch(() => {
  $("data-line").textContent = "The FAA dataset did not load. Refresh the page. If this persists, the last sync did not publish a file.";
});
