"""
Download the roll tracks for one gacha banner from a BC Godfat link.

Usage:
    python data_download.py "<godfat link>" [--force]

BC Godfat is a small fan site, so this is deliberately gentle:
  - exactly one request per run
  - only bc.godfat.org links with a seed are accepted
  - the raw HTML is cached, and a fresh cache is reused instead of re-fetching
    (use --force to override)

Output goes to data/<event id>/:
  raw.html      the page exactly as downloaded
  meta.json     seed, event id, banner name, source url, fetch time
  tracks.csv    one row per cell: position, track, roll number, guaranteed?,
                cat id, cat name, rarity, link (e.g. "-> 11B")
"""

import argparse
import csv
import json
import re
import sys
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

DATA_DIR = Path(__file__).parent / "data"
CACHE_MAX_AGE = 60 * 60  # seconds a cached download is considered fresh
USER_AGENT = "bc-route-tool/0.1 (personal hobby project; one request per run)"

POSITION_RE = re.compile(r"pick\('(\d+)([AB])(G?)'\)")
CAT_ID_RE = re.compile(r"/cats/(\d+)")
LINK_RE = re.compile(r"(<-|->)\s*(\d+[AB])")
RARITY_RE = re.compile(r"\bmajor_(\w+)")


def validate_url(url):
    """Return the parsed query if this is a usable Godfat tracks link, else raise ValueError."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or parsed.hostname != "bc.godfat.org":
        raise ValueError("Not a bc.godfat.org link.")
    if parsed.path not in ("", "/"):
        raise ValueError("Expected the main tracks page (path '/'), not a sub-page.")
    query = parse_qs(parsed.query)
    if "seed" not in query or not query["seed"][0].isdigit():
        raise ValueError("Link has no numeric 'seed' parameter.")
    if "event" not in query:
        raise ValueError("Link has no 'event' parameter (which banner to show).")
    return query


class TrackParser(HTMLParser):
    """Pulls roll cells and the selected banner name out of a Godfat tracks page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cells = []
        self.event_name = None
        self._cell = None        # cell currently being read
        self._in_cell_link = False
        self._in_selected_option = False
        self._in_event_select = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "option" and "selected" in attrs and self._in_event_select:
            self._in_selected_option = True
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
    parser = TrackParser()
    parser.feed(html)
    event_name = " ".join((parser.event_name or "").split())
    return parser.cells, event_name


def download(url, out_dir, force=False):
    raw_path = out_dir / "raw.html"
    if raw_path.exists() and not force and time.time() - raw_path.stat().st_mtime < CACHE_MAX_AGE:
        print(f"Using cached download ({raw_path}); pass --force to re-fetch.")
        return raw_path.read_text(encoding="utf-8")

    print("Fetching from bc.godfat.org (one request)...")
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(resp.text, encoding="utf-8")
    return resp.text


def main():
    sys.stdout.reconfigure(errors="replace")  # banner names contain symbols the Windows console can't print
    ap = argparse.ArgumentParser(description="Download a gacha banner's tracks from BC Godfat.")
    ap.add_argument("url", help="a bc.godfat.org link containing seed and event")
    ap.add_argument("--force", action="store_true", help="re-fetch even if a fresh cache exists")
    args = ap.parse_args()

    try:
        query = validate_url(args.url)
    except ValueError as e:
        sys.exit(f"Bad link: {e}")

    event_id = query["event"][0]
    out_dir = DATA_DIR / event_id
    html = download(args.url, out_dir, args.force)

    cells, event_name = parse_tracks(html)
    if not cells:
        sys.exit("Parsed 0 cells; the page layout may have changed.")

    fields = ["position", "roll", "track", "guaranteed", "cat_id", "cat_name", "rarity", "link"]
    with open(out_dir / "tracks.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(cells)

    meta = {
        "seed": query["seed"][0],
        "event": event_id,
        "banner": event_name,
        "source_url": args.url,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime((out_dir / "raw.html").stat().st_mtime)),
        "cells": len(cells),
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"Banner: {event_name}")
    print(f"Wrote {len(cells)} cells to {out_dir}")


if __name__ == "__main__":
    main()
