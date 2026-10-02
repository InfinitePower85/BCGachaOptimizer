"""
Download the roll tracks for one gacha banner from a BC Godfat link.

Usage:
    python backend/data_download.py "<godfat link>" [--force]

A link without an event (e.g. https://bc.godfat.org/?seed=1111) is valid: Godfat then
pre-selects a default banner -- server-side, the same for every visitor, and changing as
events rotate. The banner actually shown is read back from the page's event dropdown.

BC Godfat is a small fan site, so this is deliberately gentle:
  - only bc.godfat.org links with a seed and event are accepted
  - raw pages are cached by URL (see below) and reused while fresh (1 day; 10 minutes
    for a link without an event, since Godfat's default banner changes over time)
  - CAP: at most 100 real requests per rolling hour. Only real requests count;
    cache hits are free, and failed requests still count.
  - --force is a dev tool: it skips BOTH the freshness check and the hourly cap.
    Forced requests are still logged, so they count toward the cap for normal runs.

Files under data/seed_tracks/ (gitignored, since seeds are personal):
  <seed>/<event id>/tracks.csv   one row per cell: position, track, roll number,
                                 guaranteed?, cat id, cat name, rarity, link
  <seed>/<event id>/meta.json    seed, event, banner name, source url, fetch time
  .cache/<url hash>.html         raw pages, one per normalized URL (safe to delete)
  fetch_log.jsonl                one line per real request; drives the hourly cap
"""

import argparse
import csv
import hashlib
import io
import json
import re
import sys
import time
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from paths import DATA_DIR

SEED_TRACKS_DIR = DATA_DIR / "seed_tracks"  # per-user seed data; gitignored
TRACKS_CSV_FIELDS = ["position", "roll", "track", "guaranteed", "cat_id", "cat_name", "rarity", "link"]
CACHE_MAX_AGE = 24 * 60 * 60   # seconds a cached page counts as fresh
NO_EVENT_CACHE_MAX_AGE = 10 * 60  # same, for a link without an event (matches Godfat's own max-age)
MAX_FETCHES_PER_HOUR = 100     # cap on real requests (--force bypasses it); the server
                               # applies the same number across all users (rate_limit.py)
RATE_WINDOW = 60 * 60          # seconds; the rolling window the cap applies to
USER_AGENT = f"export my rolls - {MAX_FETCHES_PER_HOUR} Requests per hour max"

POSITION_RE = re.compile(r"pick\('(\d+)([AB])(G?)'\)")
CAT_ID_RE = re.compile(r"/cats/(\d+)")
LINK_RE = re.compile(r"(<-|->)\s*(\d+[AB])")
RARITY_RE = re.compile(r"\bmajor_(\w+)")
SAFE_NAME_RE = re.compile(r"[A-Za-z0-9_-]+")  # event ids become folder names

# Query parameters that change the page content. Anything else (pick, pos, ...) is ignored
# when deciding whether two links are "the same URL".
KEY_PARAMS = ("seed", "last", "event", "ui", "count", "lang")


QUOTE_HINT = " (If the link was cut short, put it in quotes: the shell treats '&' as 'run in background'.)"


def validate_url(url):
    """Return the parsed query if this is a usable Godfat tracks link, else raise ValueError."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or parsed.hostname != "bc.godfat.org":
        raise ValueError("Not a bc.godfat.org link.")
    if parsed.path not in ("", "/"):
        raise ValueError("Expected the main tracks page (path '/'), not a sub-page.")
    query = parse_qs(parsed.query)
    if "seed" not in query or not query["seed"][0].isdigit():
        raise ValueError("Link has no numeric 'seed' parameter." + QUOTE_HINT)
    if "event" in query and not SAFE_NAME_RE.fullmatch(query["event"][0]):
        raise ValueError("The 'event' parameter has unexpected characters.")
    return query


class RateLimitError(Exception):
    """Raised when the hourly cap on real requests has been reached."""


class PageLayoutError(Exception):
    """Raised when a fetched page doesn't have the tracks or banner we expect."""


def normalize_url(url):
    """Canonical form of a Godfat link: known content parameters only, sorted, https."""
    query = parse_qs(urlparse(url).query)
    pairs = [(k, query[k][0]) for k in sorted(KEY_PARAMS) if k in query]
    return "https://bc.godfat.org/?" + urlencode(pairs)


def cache_path(url):
    key = hashlib.sha256(normalize_url(url).encode()).hexdigest()[:16]
    return SEED_TRACKS_DIR / ".cache" / f"{key}.html"


def fetch_log_path():
    return SEED_TRACKS_DIR / "fetch_log.jsonl"


def recent_fetches(now):
    """Timestamps of real requests made within the rolling window, oldest first."""
    log = fetch_log_path()
    if not log.exists():
        return []
    times = []
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            t = json.loads(line)["t"]
        except (ValueError, KeyError, TypeError):
            continue  # skip a damaged line rather than lose the whole ledger
        if isinstance(t, (int, float)) and now - t < RATE_WINDOW:
            times.append(t)
    return sorted(times)


def check_and_record_fetch(url, now, enforce=True):
    """Refuse if the hourly cap is reached (unless enforce=False); otherwise log this
    request BEFORE it is made, so a failed or interrupted request still counts."""
    recent = recent_fetches(now)
    if enforce and len(recent) >= MAX_FETCHES_PER_HOUR:
        wait = int(recent[0] + RATE_WINDOW - now) + 1 if recent else RATE_WINDOW
        raise RateLimitError(
            f"Hourly limit reached ({MAX_FETCHES_PER_HOUR} requests in the last hour). "
            f"Next slot opens in about {wait // 60 + 1} minute(s)."
        )
    fetch_log_path().parent.mkdir(parents=True, exist_ok=True)
    with open(fetch_log_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps({"t": now, "url": normalize_url(url)}) + "\n")


class TrackParser(HTMLParser):
    """Pulls roll cells and the selected banner name out of a Godfat tracks page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cells = []
        self.event_name = None
        self.event_id = None     # value of the selected event option
        self._cell = None        # cell currently being read
        self._in_cell_link = False
        self._in_selected_option = False
        self._in_event_select = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "option" and "selected" in attrs and self._in_event_select:
            self._in_selected_option = True
            self.event_id = attrs.get("value")
        elif tag == "select":
            self._in_event_select = attrs.get("id") == "event_select"
        elif tag == "td":
            match = POSITION_RE.search(attrs.get("onclick", ""))
            # Only cells that hold a cat; "score" cells are the empty ones.
            if match and "cat" in attrs.get("class", "").split():
                rarity = RARITY_RE.search(attrs["class"])
                self._cell = {
                    "position": match.group(1) + match.group(2),
                    "roll": int(match.group(1)),
                    "track": match.group(2),
                    "guaranteed": bool(match.group(3)),
                    "rarity": rarity.group(1) if rarity else "",
                    "cat_id": "",
                    "cat_name": "",
                    "link": "",
                    "_text": [],
                }
        elif tag == "a" and self._cell is not None:
            href = attrs.get("href", "")
            id_match = CAT_ID_RE.search(href)
            if id_match:
                self._cell["cat_id"] = id_match.group(1)
            elif not self._cell["cat_name"]:
                self._in_cell_link = True  # the link wrapping the cat's name

    def handle_data(self, data):
        if self._in_selected_option:
            self.event_name = (self.event_name or "") + data
        if self._cell is not None:
            self._cell["_text"].append(data)
            if self._in_cell_link:
                self._cell["cat_name"] += data

    def handle_endtag(self, tag):
        if tag == "option":
            self._in_selected_option = False
        elif tag == "select":
            self._in_event_select = False
        elif tag == "a":
            self._in_cell_link = False
        elif tag == "td" and self._cell is not None:
            cell = self._cell
            link = LINK_RE.search("".join(cell.pop("_text")))
            cell["link"] = f"{link.group(1)} {link.group(2)}" if link else ""
            cell["cat_name"] = cell["cat_name"].strip()
            self.cells.append(cell)
            self._cell = None


def parse_tracks(html):
    """(cells, banner display text, selected event id). The id is None if the page has
    no selected event, or one that isn't safe to use as a folder name."""
    parser = TrackParser()
    parser.feed(html)
    event_name = " ".join((parser.event_name or "").split())
    event_id = parser.event_id if parser.event_id and SAFE_NAME_RE.fullmatch(parser.event_id) else None
    return parser.cells, event_name, event_id


def download(url, force=False, record_fetch=check_and_record_fetch):
    """Return the page HTML: from the URL-keyed cache if fresh, else one real request.
    force skips both the freshness check and the hourly cap. record_fetch enforces and
    records the cap before a real request; server.py swaps in a version whose counter
    survives restarts (rate_limit.godfat_fetch_gate)."""
    raw_path = cache_path(url)
    max_age = CACHE_MAX_AGE if "event" in parse_qs(urlparse(url).query) else NO_EVENT_CACHE_MAX_AGE
    if raw_path.exists() and not force and time.time() - raw_path.stat().st_mtime < max_age:
        print("Using cached page; pass --force to re-fetch.")
        return raw_path.read_text(encoding="utf-8")

    record_fetch(url, time.time(), enforce=not force)
    print("Fetching from bc.godfat.org (one request)...")
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(resp.text, encoding="utf-8")
    return resp.text


def with_event(url, event_id):
    """url with its event parameter set to event_id (added if missing)."""
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    query["event"] = [event_id]
    return parsed._replace(query=urlencode(query, doseq=True)).geturl()


def fetch_tracks(url, force=False, record_fetch=check_and_record_fetch):
    """Download (see download()) and parse a validated tracks link. Returns (cells, meta).

    A link without an event gets the one the page pre-selected (Godfat's current default);
    meta's source_url is then the explicit link, and the page is also cached under it, so
    fetching that link next reuses it."""
    html = download(url, force, record_fetch)
    cells, event_name, page_event = parse_tracks(html)
    if not cells:
        raise PageLayoutError("Parsed 0 cells; the page layout may have changed.")
    query = parse_qs(urlparse(url).query)
    url_event = query.get("event", [None])[0]
    event_id = url_event or page_event
    if not event_id:
        raise PageLayoutError("Couldn't tell which banner the page shows; the page layout may have changed.")

    source_url = url if url_event else with_event(url, event_id)
    if not url_event:
        explicit = cache_path(source_url)
        explicit.parent.mkdir(parents=True, exist_ok=True)
        explicit.write_text(html, encoding="utf-8")

    meta = build_meta(query["seed"][0], event_id, event_name, source_url, fetched_at_of(url), len(cells))
    return cells, meta


def cells_to_csv(cells):
    """Serialize parsed cells to CSV text (one row per cell, TRACKS_CSV_FIELDS header)."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=TRACKS_CSV_FIELDS)
    writer.writeheader()
    writer.writerows(cells)
    return buf.getvalue()


def build_meta(seed, event_id, event_name, source_url, fetched_at, cell_count):
    return {
        "seed": seed,
        "event": event_id,
        "banner": event_name,
        "source_url": source_url,
        "fetched_at": fetched_at,
        "cells": cell_count,
    }


def fetched_at_of(url):
    """Local timestamp of the cached page for url (set on write, so this is fetch time)."""
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(cache_path(url).stat().st_mtime))


def main():
    sys.stdout.reconfigure(errors="replace")  # banner names contain symbols the Windows console can't print
    ap = argparse.ArgumentParser(description="Download a gacha banner's tracks from BC Godfat.")
    ap.add_argument("url", help="a bc.godfat.org link containing a seed (and usually an event)")
    ap.add_argument("--force", action="store_true", help="dev tool: re-fetch even if cached, and ignore the hourly cap")
    args = ap.parse_args()

    try:
        query = validate_url(args.url)
    except ValueError as e:
        sys.exit(f"Bad link: {e}")

    try:
        cells, meta = fetch_tracks(args.url, args.force)
    except (RateLimitError, PageLayoutError) as e:
        sys.exit(str(e))

    out_dir = SEED_TRACKS_DIR / meta["seed"] / meta["event"]
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "tracks.csv", "w", newline="", encoding="utf-8") as f:
        f.write(cells_to_csv(cells))

    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    if "event" not in query:
        print(f"Link has no event, so this is Godfat's current default banner ({meta['event']})." + QUOTE_HINT)
    print(f"Banner: {meta['banner']}")
    print(f"Wrote {len(cells)} cells to {out_dir}")


if __name__ == "__main__":
    main()
