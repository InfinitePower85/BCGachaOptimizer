# Service Plan (draft)

Working notes for turning the route planner into a hosted web service. Not final; details are still coming in. We are building piece by piece, and hosting comes last.

## Architecture

```
User -> Webview (website) -> Server -> BC Godfat (fan site)
        |
        +-- IndexedDB (local, per-browser store)
```

- **Webview**: the website the user interacts with (buttons, text input, etc.). It holds the user's own data in IndexedDB.
- **Server**: the only thing that talks to Godfat. It owns rate limiting and caching.
- **BC Godfat**: the upstream data source (seed tracker). We should be a polite client.

## Data split

| Kind | What | Where |
|---|---|---|
| Software data | Roll rules, gacha pool definitions, unit metadata | Ships with the app / server |
| Local data | The user's tracks CSV (their seed's tracks) + possibly other metadata (e.g. collected units) | User's browser (IndexedDB) |

The point of the split: local data is unique to the user, but we don't want to run accounts or store it ourselves.

## Decisions so far

- **IndexedDB, not cookies or localStorage.**
  - Cookies are about 4 KB each and get sent with every request.
  - localStorage is synchronous and blocks UI rendering. IndexedDB is async.
  - IndexedDB also has far more room.
- **No accounts.** The user owns their data, and clearing browser data deletes it.
- **Export/import as the backup path.** The user downloads a file and can re-import it on another device or after clearing data. The export contains the tracks CSV plus any metadata we decide we need.
- **Rate limiting lives on the server**, likely keyed by IP, and protects Godfat.
- **The optimizer runs in Python on the server**, not in the browser.
  - The search is heavy and CPU-bound, and browser speed would vary a lot by device.
  - It avoids a JS rewrite and a second copy of the roll rules to keep in sync.
  - Bug fixes deploy in one place.
  - Cost: optimization runs need their own protection (see below).

## Rate limiting and import

Import gives us a natural relief valve: an imported tracks file needs no Godfat fetch, so it doesn't count against the limit. Either we ignore this interaction or we lean on it deliberately, for example by encouraging users to export and re-import instead of re-fetching.

The local tool already caches downloads by URL (1 day) and caps real requests at 10 per hour, with `--force` to bypass. The server version would apply the same idea, but per-IP and/or globally.

## Hosting (demo phase)

- **Backend**: Render (free web service). Expect cold starts after idle and an ephemeral disk.
- **Frontend**: static site on GitHub Pages (`docs/`, served from the `/docs` folder on `master`). The backend will need CORS for that origin.
- **Rate-limit state**: use SQLite (or similarly minimal) instead of memory or files, so counters survive restarts. On Render, that means a persistent disk, which is a paid feature, or an external free DB. To be settled when setting up Render.
- Revisit once we're out of the demo phase (likely a small VPS).

## Limiting optimization runs

Optimization is the expensive operation, so spamming it could take the server down. Planned protections (numbers TBD):

- Per-IP cap on optimization runs, separate from the Godfat fetch limit.
- Hard cap on concurrent jobs, with a queue or a "busy, try again" response.
- Per-run timeout and a ceiling on the roll limit, so one request can't run unbounded.
- Cache results keyed by input (tracks + target units + roll limit).

## Open questions

- **Rate limit shape**: per-IP, global cap to protect Godfat, or both? What numbers?
- **IP-based limiting caveats**: shared IPs (schools, mobile carriers) and proxies. Is it good enough?
- **Export format**: plain tracks CSV, or a JSON wrapper with CSV plus metadata and a version field?
- **Import validation**: imported files are untrusted input. We need to validate the schema and size before loading.
- **Optimization limits**: what numbers for per-IP runs, concurrency, timeout, and max roll limit? Sync request or async job with polling?
- **Hosting after the demo**: Render + GitHub Pages for now; what replaces them later?
- **Metadata scope**: what besides the tracks CSV belongs in local data? Collected/uncollected flags? Selected banner?
- **Tracks CSV field descriptions**: still to write (a short explanation of each column).

## Build order (tentative)

1. Finish the data download layer (Godfat fetch, cache, rate limit). In progress.
2. Pin down the roll simulation rules as a pure, tested module.
3. Route optimizer on top of the simulation.
4. Define the export/import format.
5. Minimal server wrapping the above (API + server-side rate limiting).
6. Webview with IndexedDB.
7. Pick hosting and deploy.
