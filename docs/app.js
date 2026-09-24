"use strict";

// ---- Server -------------------------------------------------------------------
// Local testing hits a local uvicorn; anywhere else (GitHub Pages) hits Render.
const LOCAL_HOSTS = ["localhost", "127.0.0.1"];
const API_BASE = LOCAL_HOSTS.includes(location.hostname)
  ? "http://127.0.0.1:8000"
  : "https://bcgachaoptimizer.onrender.com";
const API_TIMEOUT_MS = 90000; // Render's free tier can take a minute to wake up
const API_SLOW_MS = 4000;     // after this long, tell the user the server may be waking

/** Call the API. Throws with the server's detail message on a non-2xx reply or timeout. */
async function apiCall(path, { method = "GET", params, body, onSlow } = {}) {
  const controller = new AbortController();
  const slow = onSlow ? setTimeout(onSlow, API_SLOW_MS) : null;
  const timeout = setTimeout(() => controller.abort(), API_TIMEOUT_MS);
  try {
    const url = `${API_BASE}${path}${params ? "?" + new URLSearchParams(params) : ""}`;
    const res = await fetch(url, {
      method,
      signal: controller.signal,
      ...(body && { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
    });
    const responseBody = await res.json().catch(() => null);
    if (!res.ok) throw new Error((responseBody && responseBody.detail) || `Server error (${res.status}).`);
    return responseBody;
  } catch (e) {
    throw e.name === "AbortError" ? new Error("The server took too long to respond.") : e;
  } finally {
    if (slow) clearTimeout(slow);
    clearTimeout(timeout);
  }
}
const apiGet = (path, params, onSlow) => apiCall(path, { params, onSlow });

// ---- IndexedDB store --------------------------------------------------------
const DB_NAME = "bc-route-planner";
const STORE = "datasets";

function openDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(STORE, { keyPath: "id" });
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function tx(mode, fn) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const t = db.transaction(STORE, mode);
    const result = fn(t.objectStore(STORE));
    t.oncomplete = () => { db.close(); resolve(result.result); };
    t.onerror = () => { db.close(); reject(t.error); };
  });
}

const store = {
  all: () => tx("readonly", (s) => s.getAll()),
  put: (rec) => tx("readwrite", (s) => s.put(rec)),
  remove: (id) => tx("readwrite", (s) => s.delete(id)),
};

// ---- Helpers ----------------------------------------------------------------
const $ = (id) => document.getElementById(id);

function say(id, msg, kind = "") {
  const el = $(id);
  el.textContent = msg;
  el.className = "status " + kind;
}

/** Show a dataset's banner name in a reserved <p id="...">, or clear it if there isn't one. */
function showBanner(id, banner) {
  $(id).textContent = banner ? `Banner: ${banner}` : "";
}

/** Look up a saved dataset record by id (the value of a #*-dataset <select>). */
async function findDataset(id) {
  return id ? (await store.all()).find((r) => r.id === id) : undefined;
}

function countRows(csv) {
  return Math.max(0, csv.trim().split(/\r?\n/).length - 1);
}

function download(filename, text, type) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type }));
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

/** Minimal RFC 4180 CSV parser: handles quoted fields, escaped "" and commas inside quotes. */
function parseCsv(text) {
  const rows = [];
  let row = [];
  let field = "";
  let inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inQuotes) {
      if (c === '"' && text[i + 1] === '"') { field += '"'; i++; }
      else if (c === '"') inQuotes = false;
      else field += c;
    } else if (c === '"') {
      inQuotes = true;
    } else if (c === ",") {
      row.push(field); field = "";
    } else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(field); field = "";
      if (row.length > 1 || row[0] !== "") rows.push(row);
      row = [];
    } else {
      field += c;
    }
  }
  if (field !== "" || row.length) { row.push(field); rows.push(row); }
  if (!rows.length) return [];
  const header = rows[0];
  return rows.slice(1).map((r) => Object.fromEntries(header.map((h, i) => [h, r[i] ?? ""])));
}

// ---- Roll viewer --------------------------------------------------------------
// Mirrors bc.godfat.org's tracks table: one row per roll number, a column per
// track (A/B) x (normal/guaranteed) pick, cells colored by rarity.
const RARITY_LABEL = {
  rare: "Rare", supa: "Super Rare", supa_fest: "Super Rare (fest)",
  uber: "Uber Rare", uber_fest: "Uber Rare (fest)",
  legend: "Legend Rare", legend_fest: "Legend Rare (fest)",
};

function pivotByRoll(cells) {
  const rolls = new Map();
  for (const c of cells) {
    const roll = Number(c.roll);
    if (!rolls.has(roll)) rolls.set(roll, {});
    const track = rolls.get(roll);
    if (!track[c.track]) track[c.track] = {};
    track[c.track][c.guaranteed === "True" ? "guaranteed" : "normal"] = c;
  }
  return [...rolls.entries()].sort((a, b) => a[0] - b[0]);
}

function cellNode(cell) {
  const td = document.createElement("td");
  if (!cell || !cell.cat_name) { td.className = "roll-empty"; return td; }
  const name = document.createElement("span");
  name.className = "rarity-" + (cell.rarity || "none");
  name.textContent = cell.cat_name;
  name.title = RARITY_LABEL[cell.rarity] || cell.rarity || "";
  td.append(name);
  if (cell.link) {
    const link = document.createElement("span");
    link.className = "roll-link";
    link.textContent = " " + cell.link;
    td.append(link);
  }
  return td;
}

function renderRollViewer(csvText) {
  const cells = parseCsv(csvText);
  const container = $("viewer-table");
  container.replaceChildren();
  if (!cells.length) { container.textContent = "No rows to show."; return; }

  const table = document.createElement("table");
  table.className = "roll-table";
  const thead = document.createElement("thead");
  thead.innerHTML = "<tr><th>No.</th><th>A</th><th>A (guaranteed)</th><th>B</th><th>B (guaranteed)</th></tr>";
  table.append(thead);

  const tbody = document.createElement("tbody");
  for (const [roll, tracks] of pivotByRoll(cells)) {
    const tr = document.createElement("tr");
    const no = document.createElement("td");
    no.className = "roll-no";
    no.textContent = roll;
    tr.append(
      no,
      cellNode(tracks.A?.normal),
      cellNode(tracks.A?.guaranteed),
      cellNode(tracks.B?.normal),
      cellNode(tracks.B?.guaranteed),
    );
    tbody.append(tr);
  }
  table.append(tbody);
  container.append(table);
}

// ---- UI ---------------------------------------------------------------------
async function refresh() {
  const items = await store.all();
  const list = $("dataset-list");
  const selects = [$("opt-dataset"), $("viewer-dataset")];
  list.replaceChildren();
  selects.forEach((s) => s.replaceChildren());

  if (!items.length) {
    const li = document.createElement("li");
    li.className = "empty";
    li.textContent = "Nothing saved yet.";
    list.append(li);
    selects.forEach((s) => s.append(new Option("(no data)", "")));
    $("viewer-table").replaceChildren();
    showBanner("viewer-banner", "");
    showBanner("opt-dataset-banner", "");
    return;
  }

  for (const rec of items) {
    const li = document.createElement("li");
    const info = document.createElement("div");
    const name = document.createElement("div");
    name.className = "name";
    name.textContent = rec.id;
    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = `${countRows(rec.csv)} rows, saved ${new Date(rec.savedAt).toLocaleString()}`;
    info.append(name, meta);
    if (rec.meta?.banner) {
      const banner = document.createElement("div");
      banner.className = "meta banner";
      banner.textContent = rec.meta.banner;
      info.append(banner);
    }

    const del = document.createElement("button");
    del.className = "secondary";
    del.textContent = "Delete";
    del.addEventListener("click", async () => { await store.remove(rec.id); refresh(); });

    li.append(info, del);
    list.append(li);
    selects.forEach((s) => s.append(new Option(rec.id, rec.id)));
  }

  // Selects default to their first option; reflect that option's banner right away
  // rather than waiting for the user to explicitly change the selection.
  onViewerDatasetChange();
  onOptDatasetChange();
}

$("fetch-btn").addEventListener("click", async () => {
  const btn = $("fetch-btn");
  const url = $("godfat-url").value.trim();
  btn.disabled = true;
  say("fetch-status", "Fetching...");
  showBanner("fetch-banner", "");
  try {
    const body = await apiGet("/tracks", { url }, () =>
      say("fetch-status", "Still waiting. The server may be waking up, which can take up to a minute..."));
    const rec = { id: `${body.meta.seed}_${body.meta.event}`, meta: body.meta, csv: body.csv, savedAt: Date.now() };
    await store.put(rec);
    say("fetch-status", `Saved ${rec.id} (${body.meta.cells} cells).`, "ok");
    showBanner("fetch-banner", body.meta.banner);
    refresh();
    suggestEventFromBanner(body.meta.banner);
  } catch (e) {
    say("fetch-status", e.message || "Fetch failed.", "err");
  } finally {
    btn.disabled = false;
  }
});

$("viewer-btn").addEventListener("click", async () => {
  const id = $("viewer-dataset").value;
  if (!id) return say("viewer-status", "Save or import a dataset first.", "err");
  const rec = (await store.all()).find((r) => r.id === id);
  if (!rec) return say("viewer-status", "Dataset not found; it may have been deleted.", "err");
  renderRollViewer(rec.csv);
  say("viewer-status", `Showing ${id}.`, "ok");
});

$("export-btn").addEventListener("click", async () => {
  const items = await store.all();
  if (!items.length) return say("data-status", "Nothing to export.", "err");
  download("bc-route-planner-export.json", JSON.stringify({ version: 1, datasets: items }, null, 2), "application/json");
  say("data-status", `Exported ${items.length} dataset(s).`, "ok");
});

$("import-file").addEventListener("change", async (ev) => {
  const file = ev.target.files[0];
  ev.target.value = "";
  if (!file) return;
  try {
    if (file.size > 5 * 1024 * 1024) throw new Error("File is too large (5 MB max).");
    const text = await file.text();
    let records;
    if (file.name.toLowerCase().endsWith(".csv")) {
      records = [{ id: file.name.replace(/\.csv$/i, ""), meta: {}, csv: text }];
    } else {
      const data = JSON.parse(text);
      if (data.version !== 1 || !Array.isArray(data.datasets)) throw new Error("Unrecognized export file.");
      records = data.datasets;
    }
    for (const r of records) {
      if (typeof r.id !== "string" || typeof r.csv !== "string") throw new Error("Malformed dataset in file.");
      await store.put({ id: r.id, meta: r.meta || {}, csv: r.csv, savedAt: Date.now() });
    }
    say("data-status", `Imported ${records.length} dataset(s).`, "ok");
    refresh();
  } catch (e) {
    say("data-status", e.message || "Import failed.", "err");
  }
});

const OPT_MAX_TARGETS = 25; // matches the server's MAX_TARGET_UNITS

// ---- Event units (target checkboxes) -----------------------------------------
// Unit rosters come from data/gacha_pools/<event> (see fetch_gacha_units.py), grouped
// server-side by rarity with names sorted alphabetically within each group.
function renderTargetGroups(rarities) {
  const container = $("opt-targets");
  const checked = new Set(getSelectedTargets()); // preserve selections across a re-render
  container.replaceChildren();
  if (!rarities.length) {
    container.textContent = "No units found for this event.";
    return;
  }
  for (const group of rarities) {
    const fieldset = document.createElement("fieldset");
    fieldset.className = "target-group";
    const legend = document.createElement("legend");
    legend.textContent = group.rarity || "Unknown rarity";
    fieldset.append(legend);
    for (const unit of group.units) {
      // Everything (checkbox, icon, name) lives inside one <label>, so clicking the
      // icon toggles the checkbox exactly like clicking the name does.
      const label = document.createElement("label");
      label.className = "target-checkbox";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.value = unit.name;
      cb.checked = checked.has(unit.name);
      label.append(cb);

      if (unit.icon) {
        const img = document.createElement("img");
        img.className = "target-icon";
        img.src = `${API_BASE}/icons/${encodeURIComponent(unit.icon)}`;
        img.alt = "";               // decorative: the unit's name is already shown as text
        img.width = 28;
        img.height = 28;
        img.loading = "lazy";       // most units are off-screen until you scroll to them
        img.onerror = () => img.remove();
        label.append(img);
      }

      const span = document.createElement("span");
      span.textContent = unit.name;
      if (unit.description) span.title = unit.description;
      label.append(span);

      fieldset.append(label);
    }
    container.append(fieldset);
  }
}

function getSelectedTargets() {
  return [...$("opt-targets").querySelectorAll("input[type=checkbox]:checked")].map((cb) => cb.value);
}

async function loadGachaEvents() {
  const select = $("opt-event");
  select.replaceChildren();
  try {
    const events = await apiGet("/gacha-events");
    if (!events.length) {
      select.append(new Option("(no events fetched yet)", ""));
      renderTargetGroups([]);
      return;
    }
    for (const event of events) select.append(new Option(event, event));
    await loadGachaUnits(select.value);
  } catch (e) {
    say("opt-event-status", e.message || "Could not load events.", "err");
  }
}

async function loadGachaUnits(event) {
  if (!event) return renderTargetGroups([]);
  say("opt-event-status", "Loading units...");
  try {
    const body = await apiGet("/gacha-units", { event });
    renderTargetGroups(body.rarities);
    say("opt-event-status", "");
  } catch (e) {
    say("opt-event-status", e.message || "Could not load units.", "err");
  }
}

/** After fetching tracks, best-effort guess which event the banner is for (see
 * gacha_units.suggest_event()) and pre-select it -- the user can still change it. There's
 * no real id linking a Godfat banner to a wiki event name, so a failed/absent guess is
 * expected and not worth bothering the user about. */
async function suggestEventFromBanner(bannerText) {
  if (!bannerText) return;
  const select = $("opt-event");
  try {
    const { event } = await apiGet("/match-event", { text: bannerText });
    if (!event || select.value === event) return;
    if (![...select.options].some((o) => o.value === event)) return; // not a known option
    select.value = event;
    await loadGachaUnits(event);
    say("opt-event-status", `Guessed event "${event}" from the fetched banner text.`, "ok");
  } catch {
    // best-effort only
  }
}

// Both dataset <select>s show only the id (seed_eventid, e.g. "1651985299_2026-09-28_1081"),
// since a banner's full text doesn't fit well in an <option>. These look the record back
// up on each change and show its meta.banner in a reserved <p id="*-banner"> instead.
async function onViewerDatasetChange() {
  const rec = await findDataset($("viewer-dataset").value);
  showBanner("viewer-banner", rec?.meta?.banner);
}

async function onOptDatasetChange() {
  const rec = await findDataset($("opt-dataset").value);
  showBanner("opt-dataset-banner", rec?.meta?.banner);
  suggestEventFromBanner(rec?.meta?.banner);
}

$("viewer-dataset").addEventListener("change", onViewerDatasetChange);
$("opt-dataset").addEventListener("change", onOptDatasetChange);

$("opt-event").addEventListener("change", (ev) => loadGachaUnits(ev.target.value));
loadGachaEvents();

$("opt-btn").addEventListener("click", async () => {
  const datasetId = $("opt-dataset").value;
  const limit = Number($("opt-limit").value);
  const targets = getSelectedTargets();
  const out = $("opt-result");
  out.replaceChildren();
  if (!datasetId) return say("opt-status", "Save or import a dataset first.", "err");
  if (!Number.isInteger(limit) || limit < 1 || limit > 200) return say("opt-status", "Roll limit must be 1-200.", "err");
  if (!targets.length) return say("opt-status", "Check at least one target unit.", "err");
  if (targets.length > OPT_MAX_TARGETS) return say("opt-status", `At most ${OPT_MAX_TARGETS} target units.`, "err");

  const rec = (await store.all()).find((r) => r.id === datasetId);
  if (!rec) return say("opt-status", "Dataset not found; it may have been deleted.", "err");

  const btn = $("opt-btn");
  btn.disabled = true;
  say("opt-status", "Running...");
  try {
    const res = await apiCall("/optimize", {
      method: "POST",
      body: { csv: rec.csv, target_units: targets, max_rolls: limit },
      onSlow: () => say("opt-status", "Still waiting. The server may be waking up, which can take up to a minute..."),
    });
    say("opt-status", `Found ${res.score} of ${targets.length} target unit(s).`, "ok");
    for (const step of res.route) {
      const li = document.createElement("li");
      const kind = step.type === "guaranteed_eleven" ? "Guaranteed 11" : "Single roll";
      li.textContent = `${kind} @ ${step.roll}${step.track}: ${step.units.join(", ")}`;
      out.append(li);
    }
  } catch (e) {
    say("opt-status", e.message || "Optimizer failed.", "err");
  } finally {
    btn.disabled = false;
  }
});

// ---- Meow (server connection test) -------------------------------------------
const MEOW_MAX = 100;

$("meow-btn").addEventListener("click", async () => {
  const raw = $("meow-n").value.trim();
  const out = $("meow-out");
  out.value = "";
  if (!/^\d+$/.test(raw) || Number(raw) < 1 || Number(raw) > MEOW_MAX) {
    return say("meow-status", `Enter a whole number from 1 to ${MEOW_MAX}.`, "err");
  }

  const btn = $("meow-btn");
  btn.disabled = true;
  say("meow-status", "Sending...");
  try {
    out.value = await apiGet("/meow", { n: raw }, () =>
      say("meow-status", "Still waiting. The server may be waking up, which can take up to a minute..."));
    say("meow-status", "Got a reply.", "ok");
  } catch (e) {
    say("meow-status", e.message || "Request failed.", "err");
  } finally {
    btn.disabled = false;
  }
});

refresh().catch(() => say("data-status", "Browser storage is unavailable here.", "err"));
