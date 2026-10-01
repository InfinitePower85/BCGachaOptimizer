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

const LEGEND_RARITIES = new Set(["legend", "legend_fest"]);

// A guaranteed-11 (and the "two dupes in a row" mechanic more generally) can switch which
// track you're on, but only with enough rolls left to set it up -- get too close to a
// Legend Rare slot without already being lined up for it and there's no longer room to
// switch tracks to reach it, so it's effectively already missed. 12 is an approximation
// ("not exact, handles most edge cases" -- see the discussion this followed), not derived
// from the game's actual switch mechanics, and doesn't account for a banner where the
// Legend Rare is itself a target the route is already trying to hit.
const LEGEND_APPROACH_WINDOW = 12;

/** Every roll number (either track, normal or guaranteed cell) where pool has a Legend
 * Rare pull slot -- the same per-cell class LEGEND_RARITIES/.legend-pick already use,
 * not a unit's true rarity from unit_rarity.py. */
function legendRareRolls(pool) {
  const rolls = new Set();
  for (const track of ["A", "B"]) {
    for (const kind of ["normal", "guaranteed"]) {
      for (const [rollStr, cell] of Object.entries(pool[kind][track])) {
        if (LEGEND_RARITIES.has(cell.rarity)) rolls.add(Number(rollStr));
      }
    }
  }
  return rolls;
}

const isNearLegendRare = (roll, legendRolls) =>
  [...legendRolls].some((legendRoll) => legendRoll - roll >= 0 && legendRoll - roll <= LEGEND_APPROACH_WINDOW);

/** A purple (not another highlight box -- see the discussion this followed) inline marker
 * for a move/step that's within LEGEND_APPROACH_WINDOW rolls of a Legend Rare slot,
 * appended alongside whatever other status coloring a <li> already has. */
function legendWarningSpan() {
  const span = document.createElement("span");
  span.className = "legend-near-warning";
  span.textContent = " ⚠ Legend Rare in reach";
  span.title = `Within ${LEGEND_APPROACH_WINDOW} rolls of a Legend Rare pull slot on either track. `
    + "This close, there's likely no room left to track-switch into position for it -- continuing "
    + "risks missing that Legend Rare for this seed. (Approximate, not exact.)";
  return span;
}

function cellNode(cell, track, roll, kind) {
  const td = document.createElement("td");
  td.dataset.track = track;
  td.dataset.roll = roll;
  td.dataset.kind = kind;
  if (!cell || !cell.cat_name) { td.className = "roll-empty"; return td; }
  // A cell (normal or guaranteed) can be a Legend Rare, not just an Uber -- bc.godfat
  // predicts what you'd actually get at that position, and that's not limited to the
  // guaranteed-11 slot. Flag those in purple so a legend doesn't get missed for an uber
  // pulled some other way.
  if (LEGEND_RARITIES.has(cell.rarity)) td.classList.add("legend-pick");
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

// A loaded plan to follow: { steps: [{type, track, roll, units}], index, broken }.
// index is how many steps have been consumed while still on-plan (see simPushMove);
// it stops advancing once broken becomes true, since the guide's remaining steps were
// only ever valid for the position they assumed you'd be at, and one wrong-type move
// (see simPushMove's comment) leaves you somewhere else. null means no guide is active,
// which is the default and keeps every guidance UI element empty/hidden (see
// renderSim/updateGuidanceStatus) -- loading a guide is what turns "guided mode" on,
// there's no separate toggle for it.
let guidance = null;

function setGuidance(steps) {
  guidance = { steps, index: 0, broken: false };
  renderSim();
}

function clearGuidance() {
  guidance = null;
  // Past moves' guidanceStatus was only ever meaningful relative to the guide that's now
  // gone; leaving e.g. a stale "mismatch" red on them once there's nothing to have
  // mismatched against would look like a bug, not a fact about history.
  if (sim) for (const move of sim.moves) move.guidanceStatus = "unguided";
  renderSim();
}

function initSim(csvText) {
  const pool = buildSimPool(csvText);
  sim = { pool, legendRolls: legendRareRolls(pool), track: "A", roll: 1, moves: [], animating: false };
  guidance = null; // a guide's steps are tied to the pool that produced them
  say("sim-status", "", "");
  renderSim();
}

/** guidanceStatus is one of:
 *   "matched"   -- guided, on-plan, and this move's type matched the guide's step here
 *   "mismatch"  -- guided, but either this move's type didn't match the guide's step (see
 *                  below for what counts as "wrong"), or the guide was *already* broken by
 *                  an earlier mismatch -- once one move diverges, the guide's remaining
 *                  steps were only ever computed for the position that move skipped past,
 *                  so every move after it is just as off-plan even if its own type happens
 *                  to coincide with whatever the (now-irrelevant) next step says
 *   "unguided"  -- no guide step to compare against, either because no guide is loaded at
 *                  all, or because this move goes past the end of one that finished cleanly
 * A "wrong move" is specifically a single-vs-eleven mismatch (see the discussion this
 * followed): an 11-draw claims the guaranteed pick and can jump tracks in a way a 1-draw
 * fundamentally can't reproduce, so that's the one divergence worth flagging -- not which
 * unit came out, since the pool is deterministic and matches if the position does. */
function simPushMove(type, track, roll, units, to) {
  const move = { type, track, roll, units, from: { track, roll }, to };
  if (guidance) {
    // So simUndo can restore both exactly, however many wrong moves deep it's undoing.
    move.guidanceIndexBefore = guidance.index;
    move.guidanceBrokenBefore = guidance.broken;
    if (guidance.broken) {
      move.guidanceStatus = "mismatch"; // already off-plan; see the guidanceStatus doc above
    } else if (guidance.index < guidance.steps.length) {
      const matched = guidance.steps[guidance.index].type === type;
      move.guidanceStatus = matched ? "matched" : "mismatch";
      guidance.index++;
      if (!matched) guidance.broken = true;
    } else {
      move.guidanceStatus = "unguided"; // finished the plan cleanly; not broken, just done
    }
  } else {
    move.guidanceStatus = "unguided"; // no guide loaded
  }
  sim.moves.push(move);
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
  if (td) {
    td.classList.add(className);
    // "nearest" + instant: only moves the page if the cell isn't already visible, and
    // doesn't queue up a slow smooth-scroll animation for every ~70ms animation step.
    td.scrollIntoView({ block: "nearest", inline: "nearest" });
  }
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
  if (guidance && last.guidanceIndexBefore !== undefined) {
    guidance.index = last.guidanceIndexBefore;
    guidance.broken = last.guidanceBrokenBefore;
  }
  renderSim();
}

function updateSimHighlight() {
  document.querySelectorAll("#viewer-table td.sim-current, #viewer-table td.sim-current-bonus")
    .forEach((td) => td.classList.remove(...SIM_FLASH_CLASSES));
  if (!sim) return;
  const cells = document.querySelectorAll(`#viewer-table td[data-track="${sim.track}"][data-roll="${sim.roll}"]`);
  cells.forEach((td) => td.classList.add("sim-current"));
  // Keep the marker in view as it moves -- a single draw, an undo, and the end of the
  // 11-draw animation all funnel through here via renderSim().
  // only use a "nearest" scroll approach here
  cells[0]?.scrollIntoView({ block: "nearest", inline: "nearest"});


}

// name -> rarity for every unit in the game (its Normal form, which is what a gacha pull
// always yields -- see unit_rarity.py), fetched once from the server and cached here.
// This is the game's real per-unit rarity, distinct from bc.godfat's own per-cell class
// (LEGEND_RARITIES above): a unit can sit in a "legend_fest"-tier pull slot on some
// collab banner while its own true rarity is still "Uber Super Rare" (see e.g. Illyasviel,
// cat_id 365) -- the "Collected" panel wants the latter, so a user can tell what they
// actually own apart from which slot it came out of.
let unitRarityByName = {};

async function loadUnitRarities() {
  try {
    unitRarityByName = await apiGet("/unit-rarities");
  } catch {
    unitRarityByName = {}; // best-effort; the collected panel just falls back to "Unknown"
  }
  if (sim) renderSim(); // refresh with real rarities if any draws happened before this resolved
}

// Every unit name that appears in any fetched event's collab roster (see
// gacha_units.list_all_collab_unit_names()), fetched once and cached here. Backs the
// "Collected" panel's "Collab units only" checkbox -- a display-only filter, so it never
// touches sim.moves or the "Moves used" list, only which names renderSimCollected shows.
let collabUnitNames = new Set();

async function loadCollabUnitNames() {
  try {
    collabUnitNames = new Set(await apiGet("/collab-units"));
  } catch {
    collabUnitNames = new Set(); // best-effort; checking the box would then just show nothing
  }
  if (sim) renderSimCollected();
}

// Highest rarity first, since that's what a user checking "what did I get" cares about
// most. Matches unit_rarity.py's vocabulary (a unit's Normal-form rarity), not
// RARITY_LABEL's bc.godfat pull-slot scheme used elsewhere on this page.
const UNIT_RARITY_ORDER = ["Legendary Rare", "Uber Super Rare", "Super Rare", "Rare", "Special", "Basic"];
const UNIT_RARITY_CLASS = {
  Basic: "basic", Rare: "rare", "Super Rare": "supa",
  "Uber Super Rare": "uber", "Legendary Rare": "legend", Special: "special",
};

/** Render `unitNames` (with repeats -- one entry per pull, not pre-deduped) into
 * `containerId` as a rarity-grouped, deduped-with-counts list, optionally narrowed to
 * collab units via the `collabCheckboxId` checkbox. Shared by the roll simulator's
 * "Collected" panel (from sim.moves) and part 3's optimizer result (from its route) --
 * same idea, two different sources of unit names. */
function renderCollectedUnits(containerId, collabCheckboxId, unitNames) {
  const container = $(containerId);
  container.replaceChildren();
  if (!unitNames.length) return;

  const collabOnly = $(collabCheckboxId).checked;
  const counts = new Map(); // name -> count
  for (const name of unitNames) {
    if (collabOnly && !collabUnitNames.has(name)) continue; // display filter only -- the source list is untouched
    counts.set(name, (counts.get(name) || 0) + 1);
  }
  if (collabOnly && !counts.size) {
    container.textContent = "No collab units collected.";
    return;
  }

  const byRarity = new Map(); // rarity -> [[name, count], ...]
  for (const [name, count] of counts) {
    const rarity = unitRarityByName[name] || "Unknown";
    if (!byRarity.has(rarity)) byRarity.set(rarity, []);
    byRarity.get(rarity).push([name, count]);
  }

  const rarities = [...UNIT_RARITY_ORDER, ...[...byRarity.keys()].filter((r) => !UNIT_RARITY_ORDER.includes(r))];
  for (const rarity of rarities) {
    const group = byRarity.get(rarity);
    if (!group) continue;
    const total = group.reduce((n, [, count]) => n + count, 0);
    const h4 = document.createElement("h4");
    h4.className = "rarity-" + (UNIT_RARITY_CLASS[rarity] || "none");
    h4.textContent = `${rarity} (${total})`;
    container.append(h4);

    const ul = document.createElement("ul");
    for (const [name, count] of group.sort((a, b) => a[0].localeCompare(b[0]))) {
      const li = document.createElement("li");
      li.textContent = count > 1 ? `${name} ×${count}` : name;
      ul.append(li);
    }
    container.append(ul);
  }
}

function renderSimCollected() {
  renderCollectedUnits("sim-collected", "sim-collab-only", sim ? sim.moves.flatMap((m) => m.units) : []);
}

const DRAW_KIND_LABEL = { guaranteed_eleven: "Guaranteed 11", single: "Single roll" };
const DRAW_BUTTON_LABEL = { guaranteed_eleven: "11 Draw", single: "1 Draw" };

/** The status lines above the moves list: current position (always), and -- only while a
 * guide is loaded -- the last unit actually collected and what the guide says to do next
 * (or that it's finished). All empty/hidden when there's no guidance (see the :empty rules
 * in style.css). The mismatch banner (full width, spans both columns) is separate --
 * see updateMismatchBanner(). */
function updateGuidanceStatus() {
  const lastEl = $("sim-last-collected");
  const nextEl = $("sim-next-move");
  if (!sim || !guidance) {
    lastEl.textContent = "";
    nextEl.textContent = "";
    return;
  }

  const lastMove = sim.moves[sim.moves.length - 1];
  lastEl.textContent = lastMove ? `Last collected: ${lastMove.units[lastMove.units.length - 1]}` : "";

  if (guidance.broken) {
    nextEl.textContent = "Off guide -- Undo back to the wrong move to recover, or Clear guide to stop tracking it.";
  } else if (guidance.index < guidance.steps.length) {
    const next = guidance.steps[guidance.index];
    nextEl.textContent = `Next: ${DRAW_BUTTON_LABEL[next.type]} @ ${next.roll}${next.track}`;
  } else {
    nextEl.textContent = "Guide complete!";
  }
}

/** Pops up right after a mismatched move and stays up through every move after it, since
 * they're all off-plan too once the guide is broken (see simPushMove) -- rather than
 * reserving space permanently when nothing's wrong. The wording only names what the guide
 * expected for the move that actually caused the break (guidanceBrokenBefore false);
 * later moves get the more general "undo back to it" message instead, since "the guide
 * expected X" is only true of that first divergence. */
function updateMismatchBanner() {
  const banner = $("sim-mismatch-banner");
  const lastMove = sim?.moves[sim.moves.length - 1];
  if (lastMove?.guidanceStatus !== "mismatch") {
    banner.textContent = "";
    return;
  }
  banner.textContent = lastMove.guidanceBrokenBefore
    ? "Still off the guide from an earlier wrong move. Undo back to that move and redo it "
      + "right, or Clear guide if those moves already happened in-game -- then re-optimize "
      + "from your real position."
    : `Wrong move -- the guide expected ${DRAW_BUTTON_LABEL[lastMove.type === "single" ? "guaranteed_eleven" : "single"]} `
      + "here. If this was only a website slip, Undo and redo it right. If you already made "
      + "this move in-game, the guide is out of sync -- re-optimize from your real position.";
}

/** One <li> in the merged moves timeline: a <details> so only "<kind> @ <roll><track>"
 * shows by default, with the units drawn tucked behind it -- expand to see them.
 * nearLegend appends the purple "Legend Rare in reach" marker alongside whatever other
 * status coloring statusClass already gives the <li> -- see legendWarningSpan(). */
function buildMoveLi(type, track, roll, units, statusClass, note, nearLegend) {
  const li = document.createElement("li");
  li.className = statusClass;
  const details = document.createElement("details");
  const summary = document.createElement("summary");
  summary.textContent = `${DRAW_KIND_LABEL[type]} @ ${roll}${track}` + (note ? ` ${note}` : "");
  if (nearLegend) summary.append(legendWarningSpan());
  const unitsEl = document.createElement("span");
  unitsEl.className = "sim-move-units";
  unitsEl.textContent = units.join(", ");
  details.append(summary, unitsEl);
  li.append(details);
  return li;
}

function renderSim() {
  updateSimHighlight();
  renderSimCollected();
  updateGuidanceStatus();
  updateMismatchBanner();
  const movesEl = $("sim-moves");
  // The whole list is rebuilt from scratch below (simplest way to keep it in sync with
  // sim.moves + guidance), but replaceChildren() resets scrollTop to 0 -- if left alone,
  // the "nearest" scrollIntoView below would always compute against a freshly-scrolled-to-
  // top list, making it look like a forced scroll-to-bottom every time. Restoring the
  // scroll position first means "nearest" only moves the list when the next step actually
  // isn't visible, exactly like the table's own current-position marker.
  const prevScrollTop = movesEl.scrollTop;
  movesEl.replaceChildren();
  $("sim-moves-heading").textContent = guidance ? "Guided moves" : "Moves used";
  $("sim-clear-guide-btn").hidden = !guidance;

  if (!sim) {
    $("sim-position").textContent = "";
    $("sim-single-btn").disabled = true;
    $("sim-eleven-btn").disabled = true;
    $("sim-undo-btn").disabled = true;
    $("sim-set-guide-btn").disabled = true;
    return;
  }

  $("sim-position").textContent = `Position: ${sim.roll}${sim.track}`;
  $("sim-single-btn").disabled = sim.animating || !simHasSingle(sim.pool, sim.track, sim.roll);
  $("sim-eleven-btn").disabled = sim.animating || !simHasEleven(sim.pool, sim.track, sim.roll);
  $("sim-undo-btn").disabled = sim.animating || !sim.moves.length;
  $("sim-set-guide-btn").disabled = sim.animating || !sim.moves.length;

  // One continuous list: moves already made, then -- if a guide is loaded -- the steps
  // it still expects, so there's a single timeline instead of a separate "what I did" and
  // "what I'm supposed to do" panel eating twice the sidebar space. guidanceStatus (set by
  // simPushMove) colors real moves; "unguided" covers both no-guide-at-all and
  // past-the-end-of-guide the same way, per the request that those read alike.
  for (const move of sim.moves) {
    let note = "";
    if (move.guidanceStatus === "mismatch") {
      // Only the move that actually caused the break has a real "expected X" to report;
      // later ones are just as off-plan but didn't diverge from anything themselves.
      note = move.guidanceBrokenBefore
        ? "-- off guide (see the earlier wrong move)"
        : `-- wrong move (guide expected ${DRAW_BUTTON_LABEL[move.type === "single" ? "guaranteed_eleven" : "single"]})`;
    }
    const nearLegend = isNearLegendRare(move.roll, sim.legendRolls);
    movesEl.append(buildMoveLi(move.type, move.track, move.roll, move.units, `guidance-${move.guidanceStatus}`, note, nearLegend));
  }
  if (guidance) {
    let nextLi = null;
    for (let i = guidance.index; i < guidance.steps.length; i++) {
      const step = guidance.steps[i];
      // No "next" marker once broken -- the remaining steps are a frozen snapshot of what
      // the plan used to expect, not an actionable "do this now" (that's what Undo is for).
      const isNext = !guidance.broken && i === guidance.index;
      const statusClass = isNext ? "guidance-planned guidance-next" : "guidance-planned";
      const nearLegend = isNearLegendRare(step.roll, sim.legendRolls);
      const li = buildMoveLi(step.type, step.track, step.roll, step.units, statusClass, "(planned)", nearLegend);
      movesEl.append(li);
      if (isNext) nextLi = li;
    }
    movesEl.scrollTop = prevScrollTop; // restore before computing "nearest", see the comment above
    nextLi?.scrollIntoView({ block: "nearest" });
  } else {
    movesEl.scrollTop = prevScrollTop;
  }
}

$("sim-single-btn").addEventListener("click", simDrawSingle);
$("sim-eleven-btn").addEventListener("click", simDrawEleven);
$("sim-undo-btn").addEventListener("click", simUndo);
$("sim-collab-only").addEventListener("change", renderSimCollected);

// Build a guide from what you've already clicked through: e.g. work out the moves by hand
// first, then lock them in and replay the same track for real (on-site or in-game) with
// guidance on. Keeps the same pool -- only the position/history resets, not the dataset.
$("sim-set-guide-btn").addEventListener("click", () => {
  if (!sim || sim.animating || !sim.moves.length) return;
  const steps = sim.moves.map((m) => ({ type: m.type, track: m.track, roll: m.roll, units: m.units }));
  sim.track = "A";
  sim.roll = 1;
  sim.moves = [];
  setGuidance(steps);
  say("sim-status", "Guide set from your moves. Redo them for real to follow along.", "ok");
});
$("sim-clear-guide-btn").addEventListener("click", () => {
  clearGuidance();
  say("sim-status", "", "");
});

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
  say("data-status", "Fetching...");
  showBanner("data-banner", "");
  try {
    const body = await apiGet("/tracks", { url }, () =>
      say("data-status", "Still waiting. The server may be waking up, which can take up to a minute..."));
    const rec = { id: `${body.meta.seed}_${body.meta.event}`, meta: body.meta, csv: body.csv, savedAt: Date.now() };
    await store.put(rec);
    say("data-status", `Saved ${rec.id} (${body.meta.cells} cells).`, "ok");
    showBanner("data-banner", body.meta.banner);
    refresh();
    suggestEventFromBanner(body.meta.banner);
  } catch (e) {
    say("data-status", e.message || "Fetch failed.", "err");
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
  showBanner("data-banner", ""); // the fetch/import/export actions share one status line
  const items = await store.all();
  if (!items.length) return say("data-status", "Nothing to export.", "err");
  download("bc-route-planner-export.json", JSON.stringify({ version: 1, datasets: items }, null, 2), "application/json");
  say("data-status", `Exported ${items.length} dataset(s).`, "ok");
});

$("import-file").addEventListener("change", async (ev) => {
  const file = ev.target.files[0];
  ev.target.value = "";
  if (!file) return;
  showBanner("data-banner", "");
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
loadUnitRarities();
loadCollabUnitNames();

// Filled in on a successful optimize, so "Set guided moveset" (below) can hand it to the
// viewer's simulator without a second server call.
let lastOptimizeRoute = null;
let lastOptimizeDatasetId = null;
let lastOptimizeCsv = null;

function renderOptCollected() {
  const unitNames = lastOptimizeRoute ? lastOptimizeRoute.flatMap((step) => step.units) : [];
  renderCollectedUnits("opt-collected", "opt-collab-only", unitNames);
}
$("opt-collab-only").addEventListener("change", renderOptCollected);

$("opt-btn").addEventListener("click", async () => {
  const datasetId = $("opt-dataset").value;
  const limit = Number($("opt-limit").value);
  const targets = getSelectedTargets();
  const out = $("opt-result");
  out.replaceChildren();
  $("opt-set-guide-btn").disabled = true;
  lastOptimizeRoute = null;
  renderOptCollected();
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
    const legendRolls = legendRareRolls(buildSimPool(rec.csv));
    for (const step of res.route) {
      const li = document.createElement("li");
      const kind = step.type === "guaranteed_eleven" ? "Guaranteed 11" : "Single roll";
      li.textContent = `${kind} @ ${step.roll}${step.track}: ${step.units.join(", ")}`;
      if (isNearLegendRare(step.roll, legendRolls)) li.append(legendWarningSpan());
      out.append(li);
    }
    lastOptimizeRoute = res.route;
    lastOptimizeDatasetId = datasetId;
    lastOptimizeCsv = rec.csv;
    renderOptCollected();
    $("opt-set-guide-btn").disabled = !res.route.length;
  } catch (e) {
    say("opt-status", e.message || "Optimizer failed.", "err");
  } finally {
    btn.disabled = false;
  }
});

$("opt-set-guide-btn").addEventListener("click", async () => {
  if (!lastOptimizeRoute) return;
  const select = $("viewer-dataset");
  if ([...select.options].some((o) => o.value === lastOptimizeDatasetId)) select.value = lastOptimizeDatasetId;
  renderRollViewer(lastOptimizeCsv);
  initSim(lastOptimizeCsv); // fresh position/history; setGuidance below loads the plan, doesn't play it
  setGuidance(lastOptimizeRoute.map((step) => ({ type: step.type, track: step.track, roll: step.roll, units: step.units })));
  await onViewerDatasetChange();
  say("viewer-status", `Guided moveset set from the optimizer's route for ${lastOptimizeDatasetId}. Use 1 Draw / 11 Draw to follow it.`, "ok");
});

refresh().catch(() => say("data-status", "Browser storage is unavailable here.", "err"));
