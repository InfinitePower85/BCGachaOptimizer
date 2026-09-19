"use strict";

// ---- Mock API ---------------------------------------------------------------
// Stand-in for the server. Replace with real fetch() calls once the API exists.
const api = {
  async fetchTracks(url) {
    await sleep(600);
    const u = new URL(url);
    if (u.hostname !== "bc.godfat.org") throw new Error("Not a bc.godfat.org link.");
    const seed = u.searchParams.get("seed");
    const event = u.searchParams.get("event");
    if (!seed || !event) throw new Error("Link needs a seed and an event.");
    const csv = [
      "position,roll,track,guaranteed,cat_id,cat_name,rarity,link",
      "1A,1,A,False,326,Welterweight Cat,rare,",
      "1B,1,B,False,154,Cat Toaster,supa,",
      "2A,2,A,False,367,Lancer,rare,",
    ].join("\n");
    return { id: `${seed}_${event}`, meta: { seed, event, source_url: url, mock: true }, csv };
  },

  async optimize({ dataset, limit, targets }) {
    await sleep(900);
    return {
      note: "Mock result. The real optimizer runs on the server.",
      steps: [
        `Dataset ${dataset}, up to ${limit} rolls`,
        ...targets.map((t) => `Aim for: ${t}`),
        "Single roll x3, then guaranteed 11 (placeholder route)",
      ],
    };
  },
};

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
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

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

// ---- UI ---------------------------------------------------------------------
async function refresh() {
  const items = await store.all();
  const list = $("dataset-list");
  const select = $("opt-dataset");
  list.replaceChildren();
  select.replaceChildren();

  if (!items.length) {
    const li = document.createElement("li");
    li.className = "empty";
    li.textContent = "Nothing saved yet.";
    list.append(li);
    select.append(new Option("(no data)", ""));
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

    const del = document.createElement("button");
    del.className = "secondary";
    del.textContent = "Delete";
    del.addEventListener("click", async () => { await store.remove(rec.id); refresh(); });

    li.append(info, del);
    list.append(li);
    select.append(new Option(rec.id, rec.id));
  }
}

$("fetch-btn").addEventListener("click", async () => {
  const btn = $("fetch-btn");
  btn.disabled = true;
  say("fetch-status", "Fetching...");
  try {
    const rec = await api.fetchTracks($("godfat-url").value.trim());
    await store.put({ ...rec, savedAt: Date.now() });
    say("fetch-status", `Saved ${rec.id}.`, "ok");
    refresh();
  } catch (e) {
    say("fetch-status", e.message || "Fetch failed.", "err");
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
    refresh();
  } catch (e) {
    say("data-status", e.message || "Import failed.", "err");
  }
});

$("opt-btn").addEventListener("click", async () => {
  const dataset = $("opt-dataset").value;
  const limit = Number($("opt-limit").value);
  const targets = $("opt-targets").value.split("\n").map((s) => s.trim()).filter(Boolean);
  const out = $("opt-result");
  out.replaceChildren();
  if (!dataset) return say("opt-status", "Save or import a dataset first.", "err");
  if (!Number.isInteger(limit) || limit < 1 || limit > 200) return say("opt-status", "Roll limit must be 1-200.", "err");
  if (!targets.length) return say("opt-status", "Enter at least one target unit.", "err");

  const btn = $("opt-btn");
  btn.disabled = true;
  say("opt-status", "Running...");
  try {
    const res = await api.optimize({ dataset, limit, targets });
    say("opt-status", res.note, "ok");
    for (const step of res.steps) {
      const li = document.createElement("li");
      li.textContent = step;
      out.append(li);
    }
  } catch (e) {
    say("opt-status", e.message || "Optimizer failed.", "err");
  } finally {
    btn.disabled = false;
  }
});

refresh().catch(() => say("data-status", "Browser storage is unavailable here.", "err"));
