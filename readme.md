# BC Gacha Optimizer

A companion to [BC Godfat's Battle Cats seed tracker](https://bc.godfat.org). Godfat shows the rolls your seed will produce; this tool brute-force searches those rolls for the **route** (a mix of single rolls and guaranteed 11-rolls) that collects the most of the units you want within a roll budget.

**Live site:** https://infinitepower85.github.io/BCGachaOptimizer/

## Using it

1. **Datasets:** paste your bc.godfat.org link (with your seed; with no `event`, it uses the banner Godfat shows by default) to fetch its roll tracks, or import a file you exported earlier.
2. **Roll viewer:** your tracks, A and B side by side, with a roll simulator that runs entirely in your browser.
3. **Optimize:** pick the event's target units and a roll limit (and optionally a cap on guaranteed 11-rolls) to get the best route.

Your fetched tracks are stored only in your browser (IndexedDB). There are no accounts, and the server keeps no user data. Use **Export** to back them up or move them to another device.

## How Battle Cats rolls work

The rules the optimizer has to respect:

1. **Two kinds of roll:**
   - **Single roll** (1 rare ticket): take the unit at the current position.
   - **Guaranteed 11-roll** (1500 cat food): 11 rolls in a row, but the 11th is an Uber Rare instead of whatever was there. Afterwards you're on the *other* track: the switch happens after the 11th unit, before your next roll.
2. **Track switches from duplicates:** if two of the same Rare cat would come out in a row, the second is replaced by a different Rare cat, which you get, and then you switch tracks.
   - In the game you can sometimes avoid this by rolling a different gacha that's running at the same time. The simulation ignores this, since a second gacha isn't always available.

Godfat's tracks page already applies both rules: it shows where each guaranteed 11-roll lands and where duplicates switch tracks. The optimizer follows those links rather than re-implementing the rules, so it only ever needs to know your current track and roll number.

## Architecture

```
Browser (docs/, GitHub Pages) --fetch()--> FastAPI server (backend/, Render) --> bc.godfat.org
   |                                          |
   +-- IndexedDB: your tracks                 +-- Render Key Value: rate-limit counters
```

- **Frontend (`docs/`):** a static site in vanilla JS with no framework and no build step, served by GitHub Pages from `/docs` on `master`.
- **Backend (`backend/`):** a FastAPI app on Render's free tier. It's the only part that talks to Godfat.
  - `data_download.py`: fetches and parses a Godfat tracks page, with caching and an hourly request cap.
  - `route_optimizer.py`: a memoized depth-first search for the best route.
  - `gacha_units.py`, `unit_rarity.py`: event rosters and unit rarities from `data/`.
  - `rate_limit.py`: per-IP limits and the shared Godfat cap.
  - `fetch_gacha_units.py`: a developer tool that scrapes an event's roster and icons from the [Battle Cats wiki](https://battlecats.miraheze.org) into `data/`.
- **Data (`data/`):** committed reference data (event rosters, unit icons, unit rarities). `data/seed_tracks/` is a local, gitignored cache.

### Design decisions

- **The optimizer runs in Python on the server**, not in the browser. The search is CPU-heavy, browser speed varies a lot between devices, and this way there's one copy of the roll rules to keep correct.
- **User data stays in the browser**, in IndexedDB rather than cookies (about 4 KB each, sent with every request) or localStorage (synchronous, small). Export and import replace accounts. An imported file needs no Godfat fetch at all.
- **Being a polite client of Godfat:** fetched pages are cached for a day (10 minutes for links without an event, since Godfat's default banner changes), and real requests are capped at 100 per hour across all users.
- **Rate limits** are per IP, by cost:

  | Group | Limit |
  |---|---|
  | Cheap reads | 150 per 5 min |
  | `/tracks` | 5 per 15 min |
  | `/optimize` | 20 per 15 min and 5 per minute, with at most 2 runs at once |

  Counters live in Render Key Value, because the free web service loses its disk whenever it spins down. The client IP is read from the `X-Forwarded-For` entries added by Render's proxies, not the client-controlled leftmost one; see `backend/rate_limit.py`.

## Running locally

```
pip install -r requirements.txt
uvicorn server:app --app-dir backend --reload        # backend: http://127.0.0.1:8000 (API docs at /docs)
python -m http.server 8080 --directory docs          # frontend: http://localhost:8080
pytest                                               # tests (no network access needed)
```

No environment variables are needed locally: rate-limit counters fall back to memory.

Developer CLIs:
```
python backend/data_download.py "<bc.godfat.org link>" [--force]     # fetch and cache one seed's tracks
python backend/fetch_gacha_units.py <wiki url> [<url2> ...] "<Event Name>"   # add an event's roster and icons
```

### Deploying (Render)

- **Start command:** `uvicorn server:app --app-dir backend --host 0.0.0.0 --port $PORT`
- **Environment:** set `REDIS_URL` to the Render Key Value instance's internal URL. The repo is public, so secrets only ever go in Render's environment settings, never in git.

## Ideas not built yet

- Cache optimizer results by input (tracks, targets and limits).
- A per-run timeout for `/optimize`. For now, input size caps and the concurrency limit bound it.

*Not affiliated with BC Godfat or PONOS.*
