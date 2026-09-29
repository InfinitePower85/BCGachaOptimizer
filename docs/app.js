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

function cellNode(cell, track, roll, kind) {
  const td = document.createElement("td");
  td.dataset.track = track;
  td.dataset.roll = roll;
  td.dataset.kind = kind;
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
      cellNode(tracks.A?.normal, "A", roll, "normal"),
      cellNode(tracks.A?.guaranteed, "A", roll, "guaranteed"),
      cellNode(tracks.B?.normal, "B", roll, "normal"),
      cellNode(tracks.B?.guaranteed, "B", roll, "guaranteed"),
    );
    tbody.append(tr);
  }
  table.append(tbody);
  container.append(table);
}

// ---- Roll simulator -----------------------------------------------------------
// Lets the user click through single rolls / guaranteed-11s from the currently
// viewed dataset, entirely client-side (no server calls). Mirrors the roll rules
// route_optimizer.py implements server-side, but here we only ever step forward
// or undo one step -- there's no search.
//
// sim: { pool, track, roll, moves: [{type, track, roll, units, from, to}] }
// pool: { normal: {A:{roll:cell}, B:{...}}, guaranteed: {...} }, same shape as
// route_optimizer.Pool, built straight from the dataset's CSV (see buildSimPool).
let sim = null;

function buildSimPool(csvText) {
  const normal = { A: {}, B: {} };
  const guaranteed = { A: {}, B: {} };
  for (const c of parseCsv(csvText)) {
    if (c.track !== "A" && c.track !== "B") continue;
    const roll = Number(c.roll);
    const cell = { cat_name: c.cat_name, rarity: c.rarity };
    if (c.guaranteed === "True") {
      const m = /(\d+)([AB])/.exec(c.link || "");
      cell.link_target = m ? { track: m[2], roll: Number(m[1]) } : null;
      guaranteed[c.track][roll] = cell;
    } else {
      normal[c.track][roll] = cell;
    }
  }
  return { normal, guaranteed };
}

const simHasSingle = (pool, track, roll) => pool.normal[track][roll] !== undefined;

function simHasEleven(pool, track, roll) {
  const g = pool.guaranteed[track][roll];
  if (!g || !g.link_target) return false;
  for (let i = 0; i < 10; i++) {
    if (!simHasSingle(pool, track, roll + i)) return false;
  }
  return true;
}

function initSim(csvText) {
  sim = { pool: buildSimPool(csvText), track: "A", roll: 1, moves: [], animating: false };
  say("sim-status", "", "");
  renderSim();
}

function simPushMove(type, track, roll, units, to) {
  sim.moves.push({ type, track, roll, units, from: { track, roll }, to });
  sim.track = to.track;
  sim.roll = to.roll;
}

function simDrawSingle() {
  if (!sim || sim.animating || !simHasSingle(sim.pool, sim.track, sim.roll)) return;
  const { track, roll } = sim;
  const cell = sim.pool.normal[track][roll];
  simPushMove("single", track, roll, [cell.cat_name], { track, roll: roll + 1 });
  renderSim();
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const ELEVEN_STEP_MS = 70;       // fast enough to read as "11 quick draws", not a real wait
const ELEVEN_BONUS_PAUSE_MS = 260; // lingers on the guaranteed pick so the color/text swap registers

const SIM_FLASH_CLASSES = ["sim-current", "sim-current-bonus"];

/** Flash exactly one cell (by track/roll/kind), independent of sim's actual position --
 * used mid-animation, before the position itself has moved. updateSimHighlight() (the
 * static "you are here" indicator) takes over again once renderSim() next runs.
 * className picks the color: "sim-current" (yellow, the normal 10) or "sim-current-bonus"
 * (green, the guaranteed 11th) -- the color swap is the main cue that this last pick isn't
 * "next in sequence" the way the first 10 were, it's the forced bonus unit for the block. */
function flashCell(track, roll, kind, className = "sim-current") {
  document.querySelectorAll("#viewer-table td.sim-current, #viewer-table td.sim-current-bonus")
    .forEach((td) => td.classList.remove(...SIM_FLASH_CLASSES));
  const td = document.querySelector(`#viewer-table td[data-track="${track}"][data-roll="${roll}"][data-kind="${kind}"]`);
  if (td) td.classList.add(className);
}

async function simDrawEleven() {
  if (!sim || sim.animating || !simHasEleven(sim.pool, sim.track, sim.roll)) return;
  const { track, roll, pool } = sim;
  const units = [];
  for (let i = 0; i < 10; i++) units.push(pool.normal[track][roll + i].cat_name);
  units.push(pool.guaranteed[track][roll].cat_name);
  const dest = pool.guaranteed[track][roll].link_target;

  sim.animating = true;
  renderSim(); // disables the buttons for the duration of the animation

  for (let i = 0; i < 10; i++) {
    say("sim-status", `Drawing ${i + 1}/10...`, "");
    flashCell(track, roll + i, "normal");
    await sleep(ELEVEN_STEP_MS);
  }
  // The guaranteed cell lives at the top of this block's row (same roll the draw started
  // at), so the highlight jumps back up for it -- that's a real, not a mistake: this pick
  // is the block's bonus 11th unit, drawn last despite its position in the table. The
  // green color + label call that out instead of reading as backtracking.
  say("sim-status", "Guaranteed pick (bonus 11th unit)!", "ok");
  flashCell(track, roll, "guaranteed", "sim-current-bonus");
  await sleep(ELEVEN_BONUS_PAUSE_MS);

  sim.animating = false;
  simPushMove("guaranteed_eleven", track, roll, units, { track: dest.track, roll: dest.roll });
  say("sim-status", "", "");
  renderSim(); // lands the highlight on the post-jump (possibly other-track) position
}

function simUndo() {
  if (!sim || sim.animating || !sim.moves.length) return;
  const last = sim.moves.pop();
  sim.track = last.from.track;
  sim.roll = last.from.roll;
  renderSim();
}

/** Replay a route from the optimizer (see the "Simulate this route in viewer" button
 * below), which arrives as [{type, track, roll, units}] with no "next" position -- we
 * recompute each step's destination from sim's own pool, same as a manual draw would. */
function simApplyRoute(route) {
  if (!sim) return;
  for (const step of route) {
    const { track, roll, pool } = sim;
    const to = step.type === "guaranteed_eleven"
      ? pool.guaranteed[track]?.[roll]?.link_target ?? { track, roll }
      : { track, roll: roll + 1 };
    simPushMove(step.type, track, roll, step.units, to);
  }
  renderSim();
}

function updateSimHighlight() {
  document.querySelectorAll("#viewer-table td.sim-current, #viewer-table td.sim-current-bonus")
    .forEach((td) => td.classList.remove(...SIM_FLASH_CLASSES));
  if (!sim) return;
  document
    .querySelectorAll(`#viewer-table td[data-track="${sim.track}"][data-roll="${sim.roll}"]`)
    .forEach((td) => td.classList.add("sim-current"));
}

function renderSim() {
  updateSimHighlight();
  const movesEl = $("sim-moves");
  movesEl.replaceChildren();

  if (!sim) {
    $("sim-position").textContent = "";
    $("sim-single-btn").disabled = true;
    $("sim-eleven-btn").disabled = true;
    $("sim-undo-btn").disabled = true;
    return;
  }

  $("sim-position").textContent = `Position: ${sim.roll}${sim.track}`;
  $("sim-single-btn").disabled = sim.animating || !simHasSingle(sim.pool, sim.track, sim.roll);
  $("sim-eleven-btn").disabled = sim.animating || !simHasEleven(sim.pool, sim.track, sim.roll);
  $("sim-undo-btn").disabled = sim.animating || !sim.moves.length;

  for (const move of sim.moves) {
    const li = document.createElement("li");
    const kind = move.type === "guaranteed_eleven" ? "Guaranteed 11" : "Single roll";
    li.textContent = `${kind} @ ${move.roll}${move.track}: ${move.units.join(", ")}`;
    movesEl.append(li);
  }
}

$("sim-single-btn").addEventListener("click", simDrawSingle);
$("sim-eleven-btn").addEventListener("click", simDrawEleven);
$("sim-undo-btn").addEventListener("click", simUndo);

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
  initSim(rec.csv);
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

    const selectAllBtn = document.createElement("button");
    selectAllBtn.type = "button"; // not "submit": this isn't inside a <form>, but stay explicit
    selectAllBtn.className = "select-all-btn secondary";
    fieldset.append(selectAllBtn);

    const checkboxes = [];
    const updateSelectAllLabel = () => {
      const allChecked = checkboxes.every((cb) => cb.checked);
      selectAllBtn.textContent = allChecked ? "Deselect all" : "Select all";
    };

    for (const unit of group.units) {
      // Everything (checkbox, icon, name) lives inside one <label>, so clicking the
      // icon toggles the checkbox exactly like clicking the name does.
      const label = document.createElement("label");
      label.className = "target-checkbox";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.value = unit.name;
      cb.checked = checked.has(unit.name);
      cb.addEventListener("change", updateSelectAllLabel);
      checkboxes.push(cb);
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

    // Toggles based on current state: select every box in this group, unless they're
    // all already checked, in which case it clears the group instead.
    selectAllBtn.addEventListener("click", () => {
      const nextChecked = !checkboxes.every((cb) => cb.checked);
      checkboxes.forEach((cb) => { cb.checked = nextChecked; });
      updateSelectAllLabel();
    });
    updateSelectAllLabel();

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

// Filled in on a successful optimize, so "Simulate this route in viewer" (below) can
// replay it without a second server call.
let lastOptimizeRoute = null;
let lastOptimizeDatasetId = null;
let lastOptimizeCsv = null;

$("opt-btn").addEventListener("click", async () => {
  const datasetId = $("opt-dataset").value;
  const limit = Number($("opt-limit").value);
  const targets = getSelectedTargets();
  const out = $("opt-result");
  out.replaceChildren();
  $("opt-simulate-btn").disabled = true;
  lastOptimizeRoute = null;
  if (!datasetId) return say("opt-status", "Save or import a dataset first.", "err");
  if (!Number.isInteger(limit) || limit < 1 || limit > 200) return say("opt-status", "Roll limit must be 1-200.", "err");
  if (!targets.length) return say("opt-status", "Check at least one target unit.", "err");
  if (targets.length > OPT_MAX_TARGETS) return say("opt-status", `At most ${OPT_MAX_TARGETS} target units.`, "err");

  // Blank means unlimited (sent as null); otherwise a whole number >= 0, matching the
  // server's max_elevens: int | None = Field(ge=0, default=None).
  const maxElevensRaw = $("opt-max-elevens").value.trim();
  let maxElevens = null;
  if (maxElevensRaw !== "") {
    maxElevens = Number(maxElevensRaw);
    if (!Number.isInteger(maxElevens) || maxElevens < 0) {
      return say("opt-status", "Max 11-draws must be a whole number 0 or greater (or blank for unlimited).", "err");
    }
  }

  const rec = (await store.all()).find((r) => r.id === datasetId);
  if (!rec) return say("opt-status", "Dataset not found; it may have been deleted.", "err");

  const btn = $("opt-btn");
  btn.disabled = true;
  say("opt-status", "Running...");
  try {
    const res = await apiCall("/optimize", {
      method: "POST",
      body: { csv: rec.csv, target_units: targets, max_rolls: limit, max_elevens: maxElevens },
      onSlow: () => say("opt-status", "Still waiting. The server may be waking up, which can take up to a minute..."),
    });
    const elevensNote = maxElevens === null
      ? `${res.elevens_used} guaranteed-11(s) used`
      : `${res.elevens_used}/${maxElevens} guaranteed-11(s) used`;
    say("opt-status", `Found ${res.score} of ${targets.length} target unit(s), ${elevensNote}.`, "ok");
    for (const step of res.route) {
      const li = document.createElement("li");
      const kind = step.type === "guaranteed_eleven" ? "Guaranteed 11" : "Single roll";
      li.textContent = `${kind} @ ${step.roll}${step.track}: ${step.units.join(", ")}`;
      out.append(li);
    }
    lastOptimizeRoute = res.route;
    lastOptimizeDatasetId = datasetId;
    lastOptimizeCsv = rec.csv;
    $("opt-simulate-btn").disabled = !res.route.length;
  } catch (e) {
    say("opt-status", e.message || "Optimizer failed.", "err");
  } finally {
    btn.disabled = false;
  }
});

$("opt-simulate-btn").addEventListener("click", async () => {
  if (!lastOptimizeRoute) return;
  const select = $("viewer-dataset");
  if ([...select.options].some((o) => o.value === lastOptimizeDatasetId)) select.value = lastOptimizeDatasetId;
  renderRollViewer(lastOptimizeCsv);
  initSim(lastOptimizeCsv);
  simApplyRoute(lastOptimizeRoute);
  await onViewerDatasetChange();
  say("viewer-status", `Simulating the optimizer's route for ${lastOptimizeDatasetId}.`, "ok");
  $("sim-position").scrollIntoView({ behavior: "smooth", block: "center" });
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
