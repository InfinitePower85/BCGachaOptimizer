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
const iconUrl = (icon) => `${API_BASE}/icons/${encodeURIComponent(icon)}`;

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

// Which saved dataset is in use is a per-browser convenience, so plain localStorage is
// enough -- and it may be unavailable (private windows, blocked storage), hence the guards.
const ACTIVE_KEY = "bc-route-planner.active-dataset";
const ROLL_WINDOW_KEY = "bc-route-planner.roll-window";
const prefs = {
  getActive() { try { return localStorage.getItem(ACTIVE_KEY); } catch { return null; } },
  setActive(id) { try { id ? localStorage.setItem(ACTIVE_KEY, id) : localStorage.removeItem(ACTIVE_KEY); } catch { /* ignore */ } },
  getRollWindow() { try { return localStorage.getItem(ROLL_WINDOW_KEY) === "1"; } catch { return false; } },
  setRollWindow(on) { try { localStorage.setItem(ROLL_WINDOW_KEY, on ? "1" : "0"); } catch { /* ignore */ } },
};

// ---- Helpers ----------------------------------------------------------------
const $ = (id) => document.getElementById(id);

/** createElement + className + textContent in one call. */
function make(tag, className = "", text = "") {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text) el.textContent = text;
  return el;
}

function say(id, msg, kind = "") {
  const el = $(id);
  el.textContent = msg;
  el.className = "status " + kind;
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

// ---- Event roster -------------------------------------------------------------
// The selected event's collab units (data/gacha_pools/<event>, see fetch_gacha_units.py),
// grouped server-side by rarity. Shared by the optimizer's target picker, both "Collected"
// panels' banner cards, and the roll viewer's unit icons.
let eventRoster = [];           // [{rarity, units: [{name, icon, description}]}]
let rosterByName = new Map();   // name -> {rarity, icon}

function setEventRoster(rarities) {
  eventRoster = rarities;
  rosterByName = new Map();
  for (const group of rarities) {
    for (const unit of group.units) rosterByName.set(unit.name, { rarity: group.rarity, icon: unit.icon });
  }
}

// A rarity name (either vocabulary: the wiki roster's "Legend Rare" or unit_rarity.py's
// "Legendary Rare") -> the .rar-* color set used by banner cards, target tiles and dots.
const RARITY_COLOR_CLASS = {
  "Legend Rare": "rar-legend", "Legendary Rare": "rar-legend",
  "Uber Super Rare": "rar-uber", "Super Rare": "rar-super", Rare: "rar-rare",
};

function unitIcon(name, className) {
  const icon = rosterByName.get(name)?.icon;
  if (!icon) return null;
  const img = make("img", className);
  img.src = iconUrl(icon);
  img.alt = "";          // decorative: the unit's name is always shown next to it
  img.loading = "lazy";
  img.onerror = () => img.remove();
  return img;
}

// ---- Roll viewer --------------------------------------------------------------
// bc.godfat.org's tracks, laid out as two lanes mirrored around a path of roll numbers:
// [A guaranteed | A] (roll) [B | B guaranteed]. Each cell is colored by its pull-slot
// rarity class.
const RARITY_LABEL = {
  rare: "Rare", supa: "Super Rare", supa_fest: "Super Rare (fest slot)",
  uber: "Uber Rare", uber_fest: "Uber Rare (fest slot)",
  legend: "Legend Rare", legend_fest: "Legend Rare (fest slot)",
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

/** Every Legend Rare pull slot in pool (either track, normal or guaranteed cell), as
 * [{roll, track, kind, name, natural}], sorted by roll -- the same per-cell class
 * LEGEND_RARITIES/.r-legend already use, not a unit's true rarity from unit_rarity.py.
 * natural: a plain "legend" slot is a Legend Rare on any banner; a "legend_fest" slot only
 * becomes one while a "Double Legend chance" gacha is running. */
function legendRareSlots(pool) {
  const slots = [];
  for (const track of ["A", "B"]) {
    for (const kind of ["normal", "guaranteed"]) {
      for (const [rollStr, cell] of Object.entries(pool[kind][track])) {
        if (!LEGEND_RARITIES.has(cell.rarity)) continue;
        slots.push({ roll: Number(rollStr), track, kind, name: cell.cat_name, natural: cell.rarity === "legend" });
      }
    }
  }
  return slots.sort((a, b) => a.roll - b.roll || a.track.localeCompare(b.track));
}

/** The Legend Rare slots within LEGEND_APPROACH_WINDOW rolls ahead of `roll` (either track). */
const legendRaresInReach = (roll, legendSlots) =>
  legendSlots.filter((slot) => slot.roll - roll >= 0 && slot.roll - roll <= LEGEND_APPROACH_WINDOW);

/** Inline markers for a move/step within LEGEND_APPROACH_WINDOW rolls of Legend Rare
 * slots: one line per slot, naming it and its position, appended alongside whatever other
 * status coloring a <li> already has. Returns null when `slots` is empty. A natural Legend
 * Rare is darker purple, a Double-Legend-only one lighter purple, matching the roll table. */
function legendWarnings(slots) {
  if (!slots.length) return null;
  const wrap = make("span", "legend-near-warnings");
  for (const slot of slots) {
    const span = make("span", "legend-near-warning" + (slot.natural ? "" : " legend-fest"));
    const where = `${slot.roll}${slot.track}` + (slot.kind === "guaranteed" ? " (guaranteed)" : "");
    span.textContent = slot.natural
      ? `⚠ Legend Rare in reach: ${slot.name} @ ${where}`
      : `⚠ Legend Rare in reach (Double Legend chance only): ${slot.name} @ ${where}`;
    span.title = `Within ${LEGEND_APPROACH_WINDOW} rolls of this Legend Rare pull slot. `
      + "This close, there's likely no room left to track-switch into position for it -- continuing "
      + "risks missing it for this seed. (Approximate, not exact.)"
      + (slot.natural
        ? ""
        : " This slot is only a Legend Rare while a Double Legend chance gacha is running; otherwise it gives the unit shown.");
    wrap.append(span);
  }
  return wrap;
}

const PAW_SVG = '<svg class="paw" width="15" height="15" viewBox="0 0 16 16" aria-hidden="true">'
  + '<circle cx="8" cy="10.5" r="3.6" fill="currentColor"/><circle cx="3.2" cy="6" r="1.7" fill="currentColor"/>'
  + '<circle cx="6.2" cy="3.4" r="1.7" fill="currentColor"/><circle cx="9.8" cy="3.4" r="1.7" fill="currentColor"/>'
  + '<circle cx="12.8" cy="6" r="1.7" fill="currentColor"/></svg>';

// The rendered table, indexed for paintTrack(): "<track><roll><kind>" -> cell element,
// roll -> the roll number's stone. Rebuilt by renderRollViewer.
let cellEls = new Map();
let stoneEls = new Map();
const cellKey = (track, roll, kind) => `${track}${roll}${kind}`;

// The lil cat (current position) and its faded twin (where the guide's next move lands):
// one <img> each, moved into whichever cell they belong to.
const markerImg = Object.assign(make("img", "marker"), { src: "img/lil-cat.webp", alt: "You are here" });
const ghostImg = Object.assign(make("img", "ghost"), { src: "img/lil-cat.webp", alt: "Where the guide's next move lands" });

/** bc.godfat's link text ("-> 11B", "<- 12A") with real arrows. */
const prettyLink = (link) => link.replace("->", "→").replace("<-", "←");

function laneCell(cell, track, roll, kind) {
  const guaranteed = kind === "guaranteed";
  const el = make("div", `${guaranteed ? "tk" : "tile"} lane-${track.toLowerCase()}`);
  if (!cell || !cell.cat_name) { el.classList.add("empty"); return el; }
  // A cell (normal or guaranteed) can be a Legend Rare, not just an Uber -- bc.godfat
  // predicts what you'd actually get at that position, and that's not limited to the
  // guaranteed-11 slot. Guaranteed cells otherwise keep their own ticket look.
  if (!guaranteed || LEGEND_RARITIES.has(cell.rarity)) el.classList.add("r-" + (cell.rarity || "none"));
  el.title = guaranteed
    ? `${cell.cat_name}: guaranteed pick of an 11x Draw from ${roll}${track}` + (LEGEND_RARITIES.has(cell.rarity) ? ` (${RARITY_LABEL[cell.rarity]})` : "")
    : RARITY_LABEL[cell.rarity] || cell.rarity || "";
  el.dataset.name = cell.cat_name;
  if (!guaranteed) el.append(make("span", "bar"));
  const icon = unitIcon(cell.cat_name, "uicon");
  if (icon) el.append(icon);
  el.append(make("span", "nm", cell.cat_name));
  if (guaranteed) {
    if (cell.link) el.append(make("span", "jump", prettyLink(cell.link)));
    el.append(make("span", "plus", "+1"));
  } else {
    el.insertAdjacentHTML("beforeend", PAW_SVG);
  }
  cellEls.set(cellKey(track, roll, kind), el);
  return el;
}

function renderRollViewer(csvText) {
  const cells = parseCsv(csvText);
  const container = $("viewer-table");
  container.replaceChildren();
  cellEls = new Map();
  stoneEls = new Map();
  rowEls = new Map();
  if (!cells.length) { container.append(make("p", "empty", "No rows to show.")); return; }

  const lanes = make("div", "lanes");
  lanes.append(
    make("div", "lane-head g", "A · guaranteed"),
    make("div", "lane-head a", "Track A"),
    make("div", "lane-head no", "No."),
    make("div", "lane-head b", "Track B"),
    make("div", "lane-head g", "B · guaranteed"),
  );
  windowNotes.before = make("p", "window-note");
  lanes.append(windowNotes.before);
  for (const [roll, tracks] of pivotByRoll(cells)) {
    const spine = make("div", "spine");
    const stone = make("span", "stone", String(roll));
    spine.append(stone);
    stoneEls.set(roll, stone);
    const row = [
      laneCell(tracks.A?.guaranteed, "A", roll, "guaranteed"),
      laneCell(tracks.A?.normal, "A", roll, "normal"),
      spine,
      laneCell(tracks.B?.normal, "B", roll, "normal"),
      laneCell(tracks.B?.guaranteed, "B", roll, "guaranteed"),
    ];
    rowEls.set(roll, row);
    lanes.append(...row);
  }
  windowNotes.after = make("p", "window-note");
  lanes.append(windowNotes.after);
  container.append(lanes);
}

// ---- Roll window ----------------------------------------------------------------
// Optionally show only ROLL_WINDOW_SIZE rolls that follow the cat, so the whole roll
// viewer fits on one screen and is easy to scroll past. It's purely a display filter:
// the simulator and everything else still see the full dataset.
const ROLL_WINDOW_SIZE = 20;
const ROLL_WINDOW_LOOKBACK = 2; // rolls kept above the cat, so the last pull or two stays in view

let rowEls = new Map(); // roll -> that row's 5 cells, rebuilt by renderRollViewer
const windowNotes = { before: null, after: null };

/** Hide every row outside the window around `anchorRoll` (or show them all when the
 * option is off), with a note above/below saying which rolls are hidden. */
function applyRollWindow(anchorRoll) {
  const rolls = [...rowEls.keys()];
  const on = $("roll-window").checked && rolls.length > ROLL_WINDOW_SIZE;
  let start = -Infinity;
  let end = Infinity;
  if (on) {
    const first = rolls[0];
    const last = rolls[rolls.length - 1];
    start = Math.max(first, anchorRoll - ROLL_WINDOW_LOOKBACK);
    end = start + ROLL_WINDOW_SIZE - 1;
    if (end > last) { end = last; start = Math.max(first, end - ROLL_WINDOW_SIZE + 1); }
    windowNotes.before.textContent = start > first ? `▲ Rolls ${first}–${start - 1} hidden` : "";
    windowNotes.after.textContent = end < last ? `▼ Rolls ${end + 1}–${last} hidden` : "";
  }
  for (const [roll, row] of rowEls) {
    const hidden = roll < start || roll > end;
    for (const el of row) el.classList.toggle("out-of-window", hidden);
  }
  // Tighter rows while windowed, so the 20 rolls fit a typical laptop screen.
  windowNotes.before?.parentElement.classList.toggle("compact", on);
  if (!on && windowNotes.before) {
    windowNotes.before.textContent = "";
    windowNotes.after.textContent = "";
  }
}

$("roll-window").checked = prefs.getRollWindow();
$("roll-window").addEventListener("change", (ev) => {
  prefs.setRollWindow(ev.target.checked);
  paintTrack();
});

/** Re-add unit icons after the event roster changes, without rebuilding the table. */
function refreshCellIcons() {
  for (const el of cellEls.values()) {
    el.querySelector(".uicon")?.remove();
    const icon = unitIcon(el.dataset.name, "uicon");
    if (icon) el.querySelector(".nm").before(icon);
  }
}

// ---- Roll simulator -----------------------------------------------------------
// Lets the user click through single rolls / guaranteed-11s from the active dataset,
// entirely client-side (no server calls). Mirrors the roll rules route_optimizer.py
// implements server-side, but here we only ever step forward or undo one step --
// there's no search.
//
// sim: { pool, legendSlots, track, roll, moves: [{type, track, roll, units, from, to}], animating, anim }
// pool: { normal: {A:{roll:cell}, B:{...}}, guaranteed: {...} }, same shape as
// route_optimizer.Pool, built straight from the dataset's CSV (see buildSimPool).
// anim: while an 11x Draw animates, {track, roll, k} -- k is how many of the block's
// picks have been drawn so far (10 = on the guaranteed bonus pick).
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

/** Where a move/step of `type` from track/roll leaves you. */
function landing(pool, type, track, roll) {
  return type === "single" ? { track, roll: roll + 1 } : pool.guaranteed[track][roll]?.link_target || null;
}

// A loaded plan to follow: { steps: [{type, track, roll, units}], index, broken }.
// index is how many steps have been consumed while still on-plan (see simPushMove);
// it stops advancing once broken becomes true, since the guide's remaining steps were
// only ever valid for the position they assumed you'd be at, and one wrong-type move
// (see simPushMove's comment) leaves you somewhere else. null means no guide is active,
// which is the default and keeps every guidance UI element empty/hidden -- loading a
// guide is what turns "guided mode" on, there's no separate toggle for it.
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
  sim = { pool, legendSlots: legendRareSlots(pool), track: "A", roll: 1, moves: [], animating: false, anim: null };
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
  say("sim-status", "", ""); // e.g. "Guide loaded..." is stale once you've moved
  renderSim();
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const ELEVEN_STEP_MS = 90;         // fast enough to read as "11 quick draws", not a real wait
const ELEVEN_BONUS_PAUSE_MS = 450; // lingers on the guaranteed pick so the color swap registers

async function simDrawEleven() {
  if (!sim || sim.animating || !simHasEleven(sim.pool, sim.track, sim.roll)) return;
  const { track, roll, pool } = sim;
  const units = [];
  for (let i = 0; i < 10; i++) units.push(pool.normal[track][roll + i].cat_name);
  units.push(pool.guaranteed[track][roll].cat_name);
  const dest = pool.guaranteed[track][roll].link_target;

  sim.animating = true;
  renderSim(); // disables the buttons for the duration of the animation

  // The cat walks down the block leaving a paw print on each pick...
  for (let k = 0; k < 10; k++) {
    sim.anim = { track, roll, k };
    say("sim-status", `Drawing ${k + 1}/10...`, "");
    paintTrack();
    await sleep(ELEVEN_STEP_MS);
  }
  // ...then jumps back up for the guaranteed cell, which lives at the top of this block's
  // row (same roll the draw started at). It's the block's bonus 11th unit, drawn last
  // despite its position -- the green + "+1" call that out instead of reading as
  // backtracking.
  sim.anim = { track, roll, k: 10 };
  say("sim-status", "Guaranteed pick (bonus 11th unit)!", "ok");
  paintTrack();
  await sleep(ELEVEN_BONUS_PAUSE_MS);

  sim.anim = null;
  sim.animating = false;
  simPushMove("guaranteed_eleven", track, roll, units, { track: dest.track, roll: dest.roll });
  say("sim-status", "", "");
  renderSim(); // lands the cat on the post-jump (possibly other-track) position
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

/** Back to 1A with no moves; a loaded guide stays, restarted from its first step. */
function simReset() {
  if (!sim || sim.animating) return;
  sim.track = "A";
  sim.roll = 1;
  sim.moves = [];
  if (guidance) { guidance.index = 0; guidance.broken = false; }
  say("sim-status", "", "");
  renderSim();
}

/** The guide's next step, or null when there's nothing actionable (none loaded, broken, done). */
function nextGuideStep() {
  if (!guidance || guidance.broken || guidance.index >= guidance.steps.length) return null;
  return guidance.steps[guidance.index];
}

/** Bring the table in line with sim: pulled cells, the cat, the roll path, the guide. */
function paintTrack() {
  for (const el of cellEls.values()) el.classList.remove("cur", "bonus", "done", "guide", "ghosted");
  for (const stone of stoneEls.values()) stone.classList.remove("now", "past");
  markerImg.remove();
  ghostImg.remove();
  if (!sim) return;

  const markDone = (track, roll, kind) => cellEls.get(cellKey(track, roll, kind))?.classList.add("done");
  for (const m of sim.moves) {
    if (m.type === "single") { markDone(m.track, m.roll, "normal"); continue; }
    for (let i = 0; i < 10; i++) markDone(m.track, m.roll + i, "normal");
    markDone(m.track, m.roll, "guaranteed");
  }

  let markerKey = cellKey(sim.track, sim.roll, "normal");
  let markerRoll = sim.roll;
  let bonus = false;
  const anim = sim.anim;
  if (anim) {
    for (let i = 0; i < Math.min(anim.k, 10); i++) markDone(anim.track, anim.roll + i, "normal");
    bonus = anim.k >= 10;
    markerRoll = bonus ? anim.roll : anim.roll + anim.k;
    markerKey = cellKey(anim.track, markerRoll, bonus ? "guaranteed" : "normal");
  }

  for (const [roll, stone] of stoneEls) {
    if (roll === markerRoll) stone.classList.add("now");
    else if (roll < markerRoll) stone.classList.add("past");
  }
  // Anchored on the position the move started from (sim.roll doesn't change until an
  // 11x Draw finishes), so the window holds still while the cat walks its block -- the
  // block and the guaranteed pick's landing spot both fit inside it.
  applyRollWindow(sim.roll);

  const host = cellEls.get(markerKey);
  if (host) {
    host.classList.remove("done");
    host.classList.add(bonus ? "bonus" : "cur");
    markerImg.className = "marker " + (host.classList.contains("lane-a") ? "m-a" : "m-b");
    host.prepend(markerImg);
    // "nearest" + instant: only moves the page if the cell isn't already visible, and
    // doesn't queue up a slow smooth-scroll for every animation step.
    host.scrollIntoView({ block: "nearest", inline: "nearest" });
  }

  const next = !anim && nextGuideStep();
  if (next) {
    const kind = next.type === "guaranteed_eleven" ? "guaranteed" : "normal";
    cellEls.get(cellKey(next.track, next.roll, kind))?.classList.add("guide");
    const to = landing(sim.pool, next.type, next.track, next.roll);
    const ghostHost = to && cellEls.get(cellKey(to.track, to.roll, "normal"));
    if (ghostHost && ghostHost !== host) {
      ghostHost.classList.add("ghosted");
      ghostImg.className = "ghost " + (ghostHost.classList.contains("lane-a") ? "m-a" : "m-b");
      ghostHost.prepend(ghostImg);
    }
  }
}

// ---- Collected ----------------------------------------------------------------
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
  renderAllCollected(); // refresh with real rarities if any draws happened before this resolved
}

// Every unit name that appears in any fetched event's collab roster (see
// gacha_units.list_all_collab_unit_names()), fetched once and cached here. Backs the
// "Collab units only" checkboxes -- a display-only filter, so it never touches sim.moves
// or the moves list, only which names the Collected panels show.
let collabUnitNames = new Set();

async function loadCollabUnitNames() {
  try {
    collabUnitNames = new Set(await apiGet("/collab-units"));
  } catch {
    collabUnitNames = new Set(); // best-effort; checking the box then only keeps the banner cards
  }
  renderAllCollected();
}

// Highest rarity first, since that's what a user checking "what did I get" cares about
// most. Matches unit_rarity.py's vocabulary (a unit's Normal-form rarity), not
// RARITY_LABEL's bc.godfat pull-slot scheme used by the roll viewer.
const UNIT_RARITY_ORDER = ["Legendary Rare", "Uber Super Rare", "Super Rare", "Rare", "Special", "Basic"];

// Per panel, which rarity groups the user has opened/closed -- the panel is rebuilt on
// every move, and it'd be annoying for a group to snap shut each time.
const groupOpenState = { "sim-collected": new Map(), "opt-collected": new Map() };

/** Render `unitNames` (with repeats -- one entry per pull, not pre-deduped) into
 * `containerId`: first the selected event's roster as cards (pale until collected, bright
 * once you have one), then every other unit in collapsible rarity groups. Built to stay
 * compact at 100-200 pulls -- the cards are a fixed set, and the long tail of regular units
 * stays folded until wanted. Shared by the roll simulator (from sim.moves) and the
 * optimizer result (from its route). */
function renderCollectedUnits(containerId, countId, collabCheckboxId, unitNames) {
  const container = $(containerId);
  const prevScrollTop = container.scrollTop;
  container.replaceChildren();

  const collabOnly = $(collabCheckboxId).checked;
  const counts = new Map(); // name -> count
  for (const name of unitNames) counts.set(name, (counts.get(name) || 0) + 1);
  const isCollab = (name) => rosterByName.has(name) || collabUnitNames.has(name);
  let shownTotal = 0;
  for (const [name, count] of counts) if (!collabOnly || isCollab(name)) shownTotal += count;
  $(countId).textContent = unitNames.length ? `(${shownTotal})` : "";

  if (eventRoster.length) {
    const cards = [];
    let have = 0;
    for (const group of eventRoster) {
      for (const unit of group.units) {
        const count = counts.get(unit.name) || 0;
        if (count) have++;
        const card = make("div", `bt ${RARITY_COLOR_CLASS[group.rarity] || "rar-rare"}` + (count ? " has" : ""));
        card.title = `${unit.name} (${group.rarity})` + (count ? ` ×${count}` : " -- not collected");
        const icon = unitIcon(unit.name, "");
        if (icon) card.append(icon);
        if (count) card.append(make("span", "bt-count", `×${count}`));
        card.append(make("span", "bt-name", unit.name));
        cards.push(card);
      }
    }
    const grid = make("div", "bt-grid");
    grid.append(...cards);
    const section = make("div");
    section.append(make("span", "banner-head", `This banner · ${have} of ${cards.length}`), grid);
    container.append(section);
  }

  if (!unitNames.length) {
    container.append(make("p", "empty", "Nothing collected yet."));
    container.scrollTop = prevScrollTop;
    return;
  }

  const byRarity = new Map(); // rarity -> [[name, count], ...]
  for (const [name, count] of counts) {
    if (rosterByName.has(name)) continue; // already shown as a card
    if (collabOnly && !collabUnitNames.has(name)) continue; // display filter only -- the source list is untouched
    const rarity = unitRarityByName[name] || "Unknown";
    if (!byRarity.has(rarity)) byRarity.set(rarity, []);
    byRarity.get(rarity).push([name, count]);
  }

  const openState = groupOpenState[containerId];
  const rarities = [...UNIT_RARITY_ORDER, ...[...byRarity.keys()].filter((r) => !UNIT_RARITY_ORDER.includes(r))];
  let first = true;
  for (const rarity of rarities) {
    const group = byRarity.get(rarity);
    if (!group) continue;
    const details = make("details", "grp");
    details.open = openState.has(rarity) ? openState.get(rarity) : first;
    first = false;
    details.addEventListener("toggle", () => openState.set(rarity, details.open));

    const total = group.reduce((n, [, count]) => n + count, 0);
    const summary = make("summary");
    summary.append(
      make("span", `dot ${RARITY_COLOR_CLASS[rarity] || ""}`),
      make("span", "label", rarity),
      make("span", "gct", `${total} pull${total === 1 ? "" : "s"} · ${group.length} unit${group.length === 1 ? "" : "s"}`),
    );
    const ul = make("ul");
    for (const [name, count] of group.sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))) {
      const li = make("li");
      li.append(make("span", "nm", name), make("span", "n", `×${count}`));
      ul.append(li);
    }
    details.append(summary, ul);
    container.append(details);
  }
  if (collabOnly && !byRarity.size && !eventRoster.length) {
    container.append(make("p", "empty", "No collab units collected."));
  }
  container.scrollTop = prevScrollTop;
}

function renderSimCollected() {
  renderCollectedUnits("sim-collected", "sim-collected-count", "sim-collab-only",
    sim ? sim.moves.flatMap((m) => m.units) : []);
}

function renderAllCollected() {
  renderSimCollected();
  renderOptCollected();
}

// ---- Simulator sidebar --------------------------------------------------------
const DRAW_LABEL = { guaranteed_eleven: "11x Draw", single: "1x Draw" };

/** "4A" for a single, "4A → 14B" for an 11x Draw (where its guaranteed pick sends you). */
function moveWhere(pool, type, track, roll) {
  const to = type === "guaranteed_eleven" ? landing(pool, type, track, roll) : null;
  return `${roll}${track}` + (to ? ` → ${to.roll}${to.track}` : "");
}

/** The position box: where you are, what each button would give, and (only while a
 * guide is loaded) what the guide says to do next. */
function updatePositionBox() {
  const guideEl = $("sim-guide-line");
  if (!sim) {
    $("sim-pos-label").textContent = "--";
    $("sim-next-single").textContent = "Pick a dataset to start rolling.";
    $("sim-next-eleven").textContent = "";
    guideEl.textContent = "";
    return;
  }
  const { pool, track, roll } = sim;
  const single = pool.normal[track][roll];
  $("sim-pos-label").textContent = single ? `${roll}${track}` : "end";
  $("sim-next-single").textContent = single ? `1x Draw: ${single.cat_name}` : "1x Draw: past the end of this dataset";
  const g = pool.guaranteed[track][roll];
  $("sim-next-eleven").textContent = simHasEleven(pool, track, roll)
    ? `11x Draw: ${g.cat_name} guaranteed, lands on ${g.link_target.roll}${g.link_target.track}`
    : "11x Draw: not enough rolls left in this dataset";

  if (!guidance) {
    guideEl.textContent = "";
  } else if (guidance.broken) {
    guideEl.textContent = "Off guide -- Undo back to the wrong move to recover, or Clear guide to stop tracking it.";
  } else if (guidance.index < guidance.steps.length) {
    const next = guidance.steps[guidance.index];
    guideEl.textContent = `Guide says: ${DRAW_LABEL[next.type]} @ ${next.roll}${next.track}`;
  } else {
    guideEl.textContent = "Guide complete!";
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
    : `Wrong move -- the guide expected ${DRAW_LABEL[lastMove.type === "single" ? "guaranteed_eleven" : "single"]} `
      + "here. If this was only a website slip, Undo and redo it right. If you already made "
      + "this move in-game, the guide is out of sync -- re-optimize from your real position.";
}

/** One <li> in the merged moves timeline: a <details> so only "<kind> <where>" shows by
 * default, with the units drawn tucked behind it -- expand to see them. nearLegends
 * (Legend Rare slots in reach) appends a "Legend Rare in reach" marker per slot alongside
 * whatever other status coloring statusClass already gives the <li> -- see legendWarnings(). */
function buildMoveLi(type, track, roll, units, statusClass, note, nearLegends) {
  const li = make("li", statusClass);
  const details = make("details");
  const summary = make("summary");
  summary.append(make("span", "k", DRAW_LABEL[type]), " ", make("span", "w", moveWhere(sim.pool, type, track, roll)));
  if (note) summary.append(" " + note);
  const warnings = legendWarnings(nearLegends);
  if (warnings) summary.append(warnings);
  details.append(summary, make("span", "units", units.join(", ")));
  li.append(details);
  return li;
}

function renderSim() {
  paintTrack();
  renderSimCollected();
  updatePositionBox();
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
    for (const id of ["sim-single-btn", "sim-eleven-btn", "sim-undo-btn", "sim-reset-btn", "sim-set-guide-btn"]) $(id).disabled = true;
    return;
  }

  $("sim-single-btn").disabled = sim.animating || !simHasSingle(sim.pool, sim.track, sim.roll);
  $("sim-eleven-btn").disabled = sim.animating || !simHasEleven(sim.pool, sim.track, sim.roll);
  $("sim-undo-btn").disabled = sim.animating || !sim.moves.length;
  $("sim-reset-btn").disabled = sim.animating || !sim.moves.length;
  $("sim-set-guide-btn").disabled = sim.animating || !sim.moves.length;

  // One continuous list: moves already made, then -- if a guide is loaded -- the steps
  // it still expects, so there's a single timeline instead of a separate "what I did" and
  // "what I'm supposed to do" panel eating twice the sidebar space. guidanceStatus (set by
  // simPushMove) colors real moves; "unguided" covers both no-guide-at-all and
  // past-the-end-of-guide the same way, per the request that those read alike.
  sim.moves.forEach((move) => {
    let note = "";
    if (move.guidanceStatus === "mismatch") {
      // Only the move that actually caused the break has a real "expected X" to report;
      // later ones are just as off-plan but didn't diverge from anything themselves.
      note = move.guidanceBrokenBefore
        ? "-- off guide (see the earlier wrong move)"
        : `-- wrong move (guide expected ${DRAW_LABEL[move.type === "single" ? "guaranteed_eleven" : "single"]})`;
    }
    const nearLegends = legendRaresInReach(move.roll, sim.legendSlots);
    movesEl.append(buildMoveLi(move.type, move.track, move.roll, move.units, `guidance-${move.guidanceStatus}`, note, nearLegends));
  });
  if (guidance) {
    let nextLi = null;
    for (let i = guidance.index; i < guidance.steps.length; i++) {
      const step = guidance.steps[i];
      // No "next" marker once broken -- the remaining steps are a frozen snapshot of what
      // the plan used to expect, not an actionable "do this now" (that's what Undo is for).
      const isNext = !guidance.broken && i === guidance.index;
      const statusClass = isNext ? "guidance-planned guidance-next" : "guidance-planned";
      const nearLegends = legendRaresInReach(step.roll, sim.legendSlots);
      const li = buildMoveLi(step.type, step.track, step.roll, step.units, statusClass, "(planned)", nearLegends);
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
$("sim-reset-btn").addEventListener("click", simReset);
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

// ---- Datasets -----------------------------------------------------------------
// One dataset is "in use" at a time: the roll viewer shows it and the optimizer runs on it.
let activeId = prefs.getActive();
// The dataset the roll viewer / simulator currently hold: null once showDataset() has
// shown "nothing", undefined before the first refresh() so that one always renders.
let shownId;

/** "Using <dataset> · <event>" on the roll viewer and optimizer, since both now take
 * those from the Datasets section rather than having their own pickers. */
function updateUsingChips() {
  const event = $("event-select").value;
  for (const id of ["viewer-using", "opt-using"]) {
    const chip = $(id);
    chip.replaceChildren();
    if (!shownId) continue;
    chip.append("Using ", make("b", "", shownId));
    if (event) chip.append(" · ", make("b", "", event));
  }
}

/** Point the page at dataset `rec` (or nothing): roll viewer, simulator, "Using" chips. */
function showDataset(rec) {
  shownId = rec ? rec.id : null;
  updateUsingChips();
  if (lastOptimizeDatasetId !== shownId) clearOptimizeResult(); // a route only means anything for its own dataset
  if (!rec) {
    sim = null;
    guidance = null;
    cellEls = new Map();
    stoneEls = new Map();
    rowEls = new Map();
    $("viewer-table").replaceChildren(make("p", "empty", "Fetch or import a dataset to see its rolls."));
    renderSim();
    return;
  }
  renderRollViewer(rec.csv);
  initSim(rec.csv);
  suggestEventFromBanner(rec.meta?.banner);
}

/** Re-list saved datasets. Re-points the page only if the dataset in use changed (or
 * `reshow` says its contents did, e.g. a re-fetch of the same seed/event). */
async function refresh({ reshow = false } = {}) {
  const items = await store.all();
  if (!items.some((r) => r.id === activeId)) activeId = items[0]?.id ?? null;
  prefs.setActive(activeId);

  const list = $("dataset-list");
  list.replaceChildren();
  if (!items.length) list.append(make("li", "empty", "Nothing saved yet."));

  for (const rec of items) {
    const li = make("li");
    const row = make("label", "ds" + (rec.id === activeId ? " on" : ""));
    const radio = make("input");
    radio.type = "radio";
    radio.name = "active-dataset";
    radio.checked = rec.id === activeId;
    radio.addEventListener("change", () => { activeId = rec.id; refresh(); });
    row.append(radio, make("span", "name", rec.id),
      make("span", "meta", `${countRows(rec.csv)} rows · saved ${new Date(rec.savedAt).toLocaleString()}`));
    if (rec.meta?.banner) row.append(make("span", "banner", rec.meta.banner));
    if (rec.id === activeId) row.append(make("span", "in-use", "In use"));

    const del = make("button", "secondary small", "Delete");
    del.type = "button";
    del.addEventListener("click", async (ev) => {
      ev.preventDefault(); // inside the <label>: don't also select this dataset
      await store.remove(rec.id);
      refresh();
    });
    row.append(del);
    li.append(row);
    list.append(li);
  }

  if (reshow || activeId !== shownId) showDataset(items.find((r) => r.id === activeId) || null);
}

$("fetch-btn").addEventListener("click", async () => {
  const btn = $("fetch-btn");
  const url = $("godfat-url").value.trim();
  btn.disabled = true;
  say("data-status", "Fetching...");
  try {
    const body = await apiGet("/tracks", { url }, () =>
      say("data-status", "Still waiting. The server may be waking up, which can take up to a minute..."));
    const rec = { id: `${body.meta.seed}_${body.meta.event}`, meta: body.meta, csv: body.csv, savedAt: Date.now() };
    await store.put(rec);
    say("data-status", `Saved ${rec.id} (${body.meta.cells} cells) and switched to it.` + (body.meta.banner ? ` Banner: ${body.meta.banner}` : ""), "ok");
    activeId = rec.id;
    await refresh({ reshow: true });
  } catch (e) {
    say("data-status", e.message || "Fetch failed.", "err");
  } finally {
    btn.disabled = false;
  }
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
    // Re-importing the dataset in use may have replaced its contents.
    await refresh({ reshow: records.some((r) => r.id === activeId) });
  } catch (e) {
    say("data-status", e.message || "Import failed.", "err");
  }
});

// ---- Event units (target picker) ----------------------------------------------
const OPT_MAX_TARGETS = 25; // matches the server's MAX_TARGET_UNITS

const targetCheckboxes = () => [...$("opt-targets").querySelectorAll("input[type=checkbox]")];

function getSelectedTargets() {
  return targetCheckboxes().filter((cb) => cb.checked).map((cb) => cb.value);
}

/** Sync tile highlight, per-group "n/total" + Select/Deselect labels, and the overall count. */
function updateTargetCounts() {
  for (const cb of targetCheckboxes()) cb.closest(".tgt").classList.toggle("on", cb.checked);
  for (const fieldset of $("opt-targets").querySelectorAll(".tg")) {
    const boxes = [...fieldset.querySelectorAll("input[type=checkbox]")];
    const selected = boxes.filter((cb) => cb.checked).length;
    fieldset.querySelector(".tg-count").textContent = `${selected}/${boxes.length}`;
    fieldset.querySelector(".tg-toggle").textContent = selected === boxes.length ? "Deselect all" : "Select all";
  }
  const all = targetCheckboxes();
  const selected = all.filter((cb) => cb.checked).length;
  $("opt-target-count").textContent = all.length ? `${selected} of ${all.length} selected` : "";
  $("opt-select-all").disabled = !all.length || selected === all.length;
  $("opt-deselect-all").disabled = selected === 0;
}

function setAllTargets(checked) {
  targetCheckboxes().forEach((cb) => { cb.checked = checked; });
  updateTargetCounts();
}

// One fieldset per rarity (server-ordered, highest first), each unit a card colored by
// its rarity: pale until selected, bright once it is.
function renderTargetGroups() {
  const container = $("opt-targets");
  const checked = new Set(getSelectedTargets()); // preserve selections across a re-render
  container.replaceChildren();
  if (!eventRoster.length) {
    container.append(make("p", "empty", "No units found for this event."));
    updateTargetCounts();
    return;
  }
  for (const group of eventRoster) {
    const colorClass = RARITY_COLOR_CLASS[group.rarity] || "rar-rare";
    const fieldset = make("fieldset", "tg");
    const legend = make("legend");
    legend.append(make("span", `dot ${colorClass}`), group.rarity || "Unknown rarity", make("span", "muted tg-count"));
    const toggle = make("button", "secondary small tg-toggle");
    toggle.type = "button"; // not "submit": this isn't inside a <form>, but stay explicit
    const units = make("div", "tg-units");
    const boxes = [];
    for (const unit of group.units) {
      // Everything (checkbox, icon, name) lives inside one <label>, so clicking the icon
      // toggles the checkbox exactly like clicking the name does.
      const label = make("label", `tgt ${colorClass}`);
      const cb = make("input");
      cb.type = "checkbox";
      cb.value = unit.name;
      cb.checked = checked.has(unit.name);
      cb.addEventListener("change", updateTargetCounts);
      boxes.push(cb);
      label.append(cb);
      const icon = unitIcon(unit.name, "");
      if (icon) label.append(icon);
      const name = make("span", "", unit.name);
      if (unit.description) label.title = unit.description;
      label.append(name);
      units.append(label);
    }
    // Toggles based on current state: select every box in this group, unless they're
    // all already checked, in which case it clears the group instead.
    toggle.addEventListener("click", () => {
      const next = !boxes.every((cb) => cb.checked);
      boxes.forEach((cb) => { cb.checked = next; });
      updateTargetCounts();
    });
    const body = make("div", "tg-body");
    const toggleRow = make("div");
    toggleRow.append(toggle);
    body.append(toggleRow, units);
    fieldset.append(legend, body);
    container.append(fieldset);
  }
  updateTargetCounts();
}

$("opt-select-all").addEventListener("click", () => setAllTargets(true));
$("opt-deselect-all").addEventListener("click", () => setAllTargets(false));

async function loadGachaEvents() {
  const select = $("event-select");
  select.replaceChildren();
  try {
    const events = await apiGet("/gacha-events");
    if (!events.length) {
      select.append(new Option("(no events fetched yet)", ""));
      await loadGachaUnits("");
      return;
    }
    for (const event of events) select.append(new Option(event, event));
    await loadGachaUnits(select.value);
  } catch (e) {
    say("event-status", e.message || "Could not load events.", "err");
  }
}

async function loadGachaUnits(event) {
  if (!event) {
    setEventRoster([]);
  } else {
    say("event-status", "Loading units...");
    try {
      const body = await apiGet("/gacha-units", { event });
      setEventRoster(body.rarities);
      say("event-status", "");
    } catch (e) {
      say("event-status", e.message || "Could not load units.", "err");
      return;
    }
  }
  renderTargetGroups();
  refreshCellIcons();
  renderAllCollected();
  updateUsingChips();
}

/** When a dataset comes into use, best-effort guess which event its banner is for (see
 * gacha_units.suggest_event()) and pre-select it -- the user can still change it. There's
 * no real id linking a Godfat banner to a wiki event name, so a failed/absent guess is
 * expected and not worth bothering the user about. */
async function suggestEventFromBanner(bannerText) {
  if (!bannerText) return;
  const select = $("event-select");
  try {
    const { event } = await apiGet("/match-event", { text: bannerText });
    if (!event || select.value === event) return;
    if (![...select.options].some((o) => o.value === event)) return; // not a known option
    select.value = event;
    await loadGachaUnits(event);
    say("event-status", `Guessed event "${event}" from the dataset's banner text.`, "ok");
  } catch {
    // best-effort only
  }
}

$("event-select").addEventListener("change", (ev) => loadGachaUnits(ev.target.value));

// ---- Optimize -----------------------------------------------------------------
// Filled in on a successful optimize, so "Use as guide" (below) can hand it to the
// simulator without a second server call.
let lastOptimizeRoute = null;
let lastOptimizeDatasetId = null;

function renderOptCollected() {
  const unitNames = lastOptimizeRoute ? lastOptimizeRoute.flatMap((step) => step.units) : [];
  renderCollectedUnits("opt-collected", "opt-collected-count", "opt-collab-only", unitNames);
}
$("opt-collab-only").addEventListener("change", renderOptCollected);

function clearOptimizeResult() {
  lastOptimizeRoute = null;
  lastOptimizeDatasetId = null;
  $("opt-result").replaceChildren();
  $("opt-results").hidden = true;
  $("opt-set-guide-btn").disabled = true;
  say("opt-status", "");
}

/** One route step: "<kind> <where> <what>", expandable to every unit it draws. */
function buildRouteLi(step, pool, legendSlots) {
  const eleven = step.type === "guaranteed_eleven";
  const details = make("details");
  const summary = make("summary");
  summary.append(
    make("span", "k", DRAW_LABEL[step.type]),
    make("span", "w", moveWhere(pool, step.type, step.track, step.roll)),
    make("span", "", eleven ? `${step.units[step.units.length - 1]} + ${step.units.length - 1} more` : step.units[0]),
    make("span", "more", "units"),
  );
  const warnings = legendWarnings(legendRaresInReach(step.roll, legendSlots));
  if (warnings) summary.append(warnings);
  const ol = make("ol");
  step.units.forEach((unit, i) => {
    const li = make("li", "", unit);
    if (eleven && i === step.units.length - 1) li.append(make("span", "gtag", "guaranteed"));
    ol.append(li);
  });
  details.append(summary, ol);
  const li = make("li");
  li.append(details);
  return li;
}

$("opt-btn").addEventListener("click", async () => {
  const limit = Number($("opt-limit").value);
  const targets = getSelectedTargets();
  clearOptimizeResult();
  renderOptCollected();
  if (!activeId) return say("opt-status", "Save or import a dataset first.", "err");
  if (!Number.isInteger(limit) || limit < 1 || limit > 200) return say("opt-status", "Roll limit must be 1-200.", "err");
  if (!targets.length) return say("opt-status", "Select at least one target unit.", "err");
  if (targets.length > OPT_MAX_TARGETS) return say("opt-status", `At most ${OPT_MAX_TARGETS} target units.`, "err");

  // Blank means unlimited (sent as null); otherwise a whole number >= 0, matching the
  // server's max_elevens: int | None = Field(ge=0, default=None).
  const maxElevensRaw = $("opt-max-elevens").value.trim();
  let maxElevens = null;
  if (maxElevensRaw !== "") {
    maxElevens = Number(maxElevensRaw);
    if (!Number.isInteger(maxElevens) || maxElevens < 0) {
      return say("opt-status", "Max 11x Draws must be a whole number 0 or greater (or blank for unlimited).", "err");
    }
  }

  const datasetId = activeId;
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
    const pool = buildSimPool(rec.csv);
    // The roll limit caps the roll number reached (see route_optimizer.solve), not the
    // number of units pulled -- an 11x Draw pulls 11 -- so report both separately.
    const pulls = res.route.reduce((n, step) => n + step.units.length, 0);
    const last = res.route[res.route.length - 1];
    const end = last && landing(pool, last.type, last.track, last.roll);
    const elevensNote = `11x Draws used: ${res.elevens_used}` + (maxElevens === null ? "" : `/${maxElevens}`);
    say("opt-status", `Done: ${res.score} of ${targets.length} target unit(s) collected · ${pulls} pulls`
      + (end ? `, ending at ${end.roll}${end.track}` : "") + ` · ${elevensNote}.`, "ok");

    const legendSlots = legendRareSlots(pool);
    const out = $("opt-result");
    res.route.forEach((step) => out.append(buildRouteLi(step, pool, legendSlots)));
    if (!res.route.length) out.append(make("li", "empty", "No route collects any of the targets within the limit."));
    lastOptimizeRoute = res.route;
    lastOptimizeDatasetId = datasetId;
    $("opt-results").hidden = false;
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
  const route = lastOptimizeRoute;
  // The route only applies to the dataset it was computed for; showDataset() clears it
  // on a switch, so the simulator should already hold that dataset.
  if (!sim || sim.animating || shownId !== lastOptimizeDatasetId) return;
  // Fresh position/history; setGuidance loads the plan, it doesn't play it.
  sim.track = "A";
  sim.roll = 1;
  sim.moves = [];
  setGuidance(route.map((step) => ({ type: step.type, track: step.track, roll: step.roll, units: step.units })));
  say("sim-status", "Guide loaded from the optimizer's route. Use 1x Draw / 11x Draw to follow it.", "ok");
  $("viewer-table").scrollIntoView({ behavior: "smooth", block: "start" });
});

// ---- Startup ------------------------------------------------------------------
renderSim();
loadGachaEvents();
loadUnitRarities();
loadCollabUnitNames();
refresh().catch(() => say("data-status", "Browser storage is unavailable here.", "err"));
