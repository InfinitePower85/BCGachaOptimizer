# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A companion tool to [BC Godfat's Battle Cats seed tracker](https://bc.godfat.org): given a predicted roll sequence for a seed, it brute-force searches for the roll route (single rolls + guaranteed-11s) that collects the most target units within a roll budget. FastAPI backend + a static vanilla-JS frontend (GitHub Pages), no build step on either side.

## Commands

Run from the repo root (`pytest.ini` sets `pythonpath = backend`):

```
pytest                                    # full suite
pytest tests/test_route_optimizer.py      # one file
pytest tests/test_route_optimizer.py::test_max_elevens_zero_blocks_a_target_only_reachable_via_a_guaranteed_eleven   # one test
```

No lint or build step is configured for either the Python side or `docs/`.

Local dev (two terminals, from repo root):
```
uvicorn server:app --app-dir backend --reload   # backend, http://127.0.0.1:8000
python -m http.server 8080 --directory docs   # frontend, http://localhost:8080 (must differ from 8000)
```

CLI tools (see each file's docstring for full usage):
```
python backend/data_download.py "<bc.godfat.org link>" [--force]      # fetch/cache one seed's rolled tracks
python backend/fetch_gacha_units.py <url> [<url2> ...] <event_name>    # scrape a wiki gacha page's unit roster + icons
```

## Architecture

All Python modules live in `backend/` as flat, top-level modules that import each other by bare name (`from data_download import ...`), which works because `--app-dir backend`, `pytest.ini`'s `pythonpath` and running a CLI as `python backend/x.py` each put `backend/` on `sys.path`. `data/` stays at the repo root; `backend/paths.py`'s `DATA_DIR` is the one place that locates it (`tests/test_paths.py` checks the real folders resolve).

Three independent pipelines meet in `server.py`:

1. **Godfat pipeline** (`data_download.py`): fetches a user's *personal, seed-specific* rolled-tracks page from bc.godfat.org and parses it into `tracks.csv` rows (`position, roll, track, guaranteed, cat_id, cat_name, rarity, link`). The `event` param is optional: without it Godfat pre-selects a default banner server-side (no cookies; same for everyone, rotates over time), so `fetch_tracks()` reads the event id from the page's selected `event_select` option, records the explicit link as `source_url`, and caches such links for only 10 minutes. Owns its own politeness limits: 1-day URL-keyed cache, 100 real requests/hour, `--force` bypasses both (still logged, still counts). `link` encodes where a guaranteed-11 lands (`+10`/`+11` A↔B math) — deliberately not re-derived elsewhere; every consumer just follows it.
2. **Wiki pipeline** (`fetch_gacha_units.py`): scrapes a `battlecats.miraheze.org` gacha-drop page for an event's *shared, non-user-specific* unit roster — writes `data/gacha_pools/<event>/<event>_units_<n>.csv` (`rarity, name, description, cat_id`; `n` = banner number, since some events have two) and downloads each unit's icon to `data/icons/unit_icons/<unit name>.<ext>`. Run manually/offline by a developer, not triggered by end users, then committed. Event units are told apart from the game's regular pool units structurally (a `.template-gacha-description` wrapper the wiki gives collab units), never by hardcoding names. Only each unit's Normal form is kept.
3. **Optimizer** (`route_optimizer.py`): `solve(pool, target_units, max_rolls, max_elevens=None, ...)` runs a DFS memoized on `(track, roll, mask-of-targets-collected, elevens_used)` to find the best route. `max_rolls` and `max_elevens` are independent resources with a real trade-off frontier between them (not a joint minimum) — `min_rolls_for_full_collection`/`min_elevens_for_full_collection` each binary-search one axis with the other pinned, which is valid because score is monotonic non-decreasing in each axis individually (increasing either only ever adds an available action). See the module docstring before changing the search.

`gacha_units.py` sits between pipelines 2 and 3's data and the frontend: lists fetched events, merges an event's banner CSVs (de-duped by name), groups/sorts units by rarity, resolves each unit's icon filename, and `suggest_event()` — a best-effort heuristic that matches a fetched Godfat banner's *display text* against known events' unit names (whole-word match) to pre-select the frontend's event dropdown. There is no real ID linking a Godfat banner to a wiki event name; this is why the heuristic exists.

`server.py` (FastAPI) is a thin HTTP layer over all of the above, plus CORS for the GitHub Pages origin + any localhost port, and a `StaticFiles` mount at `/icons` with a long `Cache-Control` (icons don't change once fetched). Rate limiting lives in `rate_limit.py`: per-IP fixed-window limits by route group (`low` / `tracks` / `optimize`; `/` and `/icons` are unlimited), a shared rolling cap on real Godfat fetches (replacing `data_download`'s file-based cap on the server, via `download(record_fetch=...)`), and a concurrency cap on `/optimize`. Counters live in Render Key Value when `REDIS_URL` is set, else in memory (also the fallback on Redis errors). The client IP comes from `ClientIPMiddleware`, which takes the `X-Forwarded-For` entry `TRUSTED_PROXY_HOPS` from the right (default 3 on Render: client, Cloudflare edge, Render internal — verified via `/whoami`) — never the client-controlled leftmost one, and never uvicorn's `--forwarded-allow-ips="*"`, which returns exactly that. `/whoami` (only with `ENABLE_WHOAMI=1`) checks this against the live proxy. Beyond those short-lived counters, the server does not persist anything per-user — the browser is the only place a user's fetched tracks CSV lives (IndexedDB; see `docs/app.js`'s `store`), by design: no accounts, no server-side user data. `docs/` is the static frontend (no framework, no build step) that calls the API via `fetch()` and renders results with vanilla DOM calls.

### Data layout

- `data/gacha_pools/`, `data/icons/` — wiki-derived reference data, committed to git.
- `data/seed_tracks/` — gitignored (personal/seed-specific): the Godfat page cache, per-`seed/event` `tracks.csv` + `meta.json`, and `fetch_log.jsonl` (drives the hourly cap).

### Public repo — secrets come from environment variables

This repo is public on GitHub. Never commit credentials, connection strings (e.g. the Render Key Value / Redis URL), or `.env` files. Read them from environment variables set in Render's dashboard, and have tests use fakes rather than real credentials.

### Testing conventions

No real network calls: `requests.get` is monkeypatched per test (fakes recording calls / returning fixture HTML), and saved sample pages live in `tests/fixtures/`. Tests that touch disk monkeypatch the relevant module-level path constant (e.g. `GACHA_POOLS_DIR`, `ICONS_DIR`, `SEED_TRACKS_DIR`) to a `tmp_path`, never the real `data/`. Where a memoized/DFS result needs cross-checking, an unmemoized brute-force reference search is run over the same random small pools instead of hand-computing expected values.
