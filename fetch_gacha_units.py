"""
Fetch the event (collab) units listed on a Battle Cats wiki gacha drop page and save
them as CSV.

Usage:
    python fetch_gacha_units.py <event_name> <url> [<url2> ...]

Example:
    python fetch_gacha_units.py "Fate Stay Night" "https://battlecats.miraheze.org/wiki/..."

Pages like https://battlecats.miraheze.org/wiki/.../Gacha_Drop list units under an
"Event" section, one rarity-tier table at a time. Within a tier, the collab/event units
are rendered with a name + description + image (".template-gacha-description" blocks);
the regular, non-collab units that also appear in the same pool (e.g. a generic
"Bodhisattva Cat") are rendered as a bare icon strip plus a plain-text name list, with no
description. That structural difference -- not any unit's name -- is what this script
uses to keep the regular pool units out, per the task ("don't hardcode unit names").

Only each event unit's Normal form is recorded (not its Evolved/True form).

For each url given (in the order given), writes:
    data/gacha_pools/<event_name>/<event_name>_units_<n>.csv   (n = 1, 2, ... starting at 1)

CSV columns: rarity, name, description, cat_id (cat_id is blank if the page has none).

Also downloads each unit's Normal-form icon (the same image used to find cat_id) to:
    data/icons/unit_icons/<unit name>.<ext>
so a unit's data can be looked up by the same name used in the CSV. An icon already
saved from a previous run (or an earlier unit/banner in this run) is not re-downloaded.
"""

import argparse
import csv
import io
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import requests

GACHA_POOLS_DIR = Path(__file__).parent / "data" / "gacha_pools"
ICONS_DIR = Path(__file__).parent / "data" / "icons" / "unit_icons"
UNIT_CSV_FIELDS = ["rarity", "name", "description", "cat_id"]
USER_AGENT = "fetch_gacha_units.py - one-off Battle Cats wiki gacha page fetcher"

# Wiki unit images are named like ".../Uni362_f00.png" (Normal form) or
# ".../Uni362_c00.png" (Evolved form); the digits are the game's cat id, sometimes
# zero-padded (e.g. Uni030). Leading zeros are stripped for consistency with the plain
# integer cat_id used elsewhere in this project (see data_download.py's CAT_ID_RE).
CAT_ID_RE = re.compile(r"Uni0*(\d+)_")

# Characters Windows won't allow in a file/folder name.
INVALID_NAME_CHARS_RE = re.compile(r'[<>:"/\\|?*]')

# div classes inside the Event table that this parser tracks; anything else (including
# the "cropped-icon" divs used for the non-event pool units) is left as a plain marker
# so div nesting still balances, but nothing is captured from it.
TRACKED_DIV_CLASSES = (
    "template-gacha-description",
    "gacha-unit-form",
    "gacha-unit-header",
    "gacha-unit-img",
    "gacha-unit-name",
    "gacha-unit-description",
)


class GachaUnitParser(HTMLParser):
    """Pulls event-only, Normal-form units out of a battlecats.miraheze.org gacha page.

    Scoped to the "Event" section (between <h2 id="Event"> and the next <h2>). Within
    it, each event unit is a ".template-gacha-description" block containing one
    ".gacha-unit-form" per evolution stage (Normal, Evolved, ...); only the Normal one
    is kept. div nesting is tracked with a small stack rather than full HTML tree
    parsing, since these blocks are shallow and don't nest further divs of interest.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.units = []
        self._in_event = False
        self._rarity = None
        self._heading_text = None     # accumulator while inside a gacha-table-heading td
        self._div_stack = []          # one marker per open <div> seen while in the event section
        self._form = None             # accumulator for the gacha-unit-form currently being read

    def _top(self):
        return self._div_stack[-1] if self._div_stack else None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "h2":
            self._in_event = attrs.get("id") == "Event"
            return
        if not self._in_event:
            return

        if tag == "td":
            if "gacha-table-heading" in attrs.get("class", "").split():
                self._heading_text = ""
        elif tag == "div":
            classes = attrs.get("class", "").split()
            marker = next((c for c in TRACKED_DIV_CLASSES if c in classes), None)
            self._div_stack.append(marker)
            if marker == "gacha-unit-form":
                self._form = {"header": "", "name": "", "description": [], "cat_id": "", "image_src": ""}
        elif tag == "img" and self._top() == "gacha-unit-img" and self._form is not None:
            src = attrs.get("src", "")
            self._form["image_src"] = src
            match = CAT_ID_RE.search(src)
            if match:
                self._form["cat_id"] = match.group(1)
        elif tag == "br" and self._top() == "gacha-unit-description" and self._form is not None:
            self._form["description"].append(" ")

    def handle_data(self, data):
        if self._heading_text is not None:
            self._heading_text += data
        elif self._form is not None:
            top = self._top()
            if top == "gacha-unit-header":
                self._form["header"] += data
            elif top == "gacha-unit-name":
                self._form["name"] += data
            elif top == "gacha-unit-description":
                self._form["description"].append(data)

    def handle_endtag(self, tag):
        if tag == "td" and self._heading_text is not None:
            # strip the leading "▼" marker and surrounding whitespace, without
            # hardcoding that specific character
            rarity = re.sub(r"^\W+", "", self._heading_text.strip())
            if rarity:
                self._rarity = rarity
            self._heading_text = None
        elif tag == "div" and self._div_stack:
            marker = self._div_stack.pop()
            if marker == "gacha-unit-form" and self._form is not None:
                self._finish_form()

    def _finish_form(self):
        form, self._form = self._form, None
        if form["header"].strip() != "Normal":
            return
        name = form["name"].strip()
        if not name:
            return
        description = " ".join("".join(form["description"]).split())
        self.units.append({
            "rarity": self._rarity or "",
            "name": name,
            "description": description,
            "cat_id": form["cat_id"],
            "image_url": original_image_url(form["image_src"]),
        })


def original_image_url(src):
    """Convert a MediaWiki thumbnail URL (".../thumb/a/bc/File.png/100px-File.png") to
    the full-size original (".../a/bc/File.png"). Left unchanged if it doesn't look like
    a thumbnail path (e.g. already a direct file URL)."""
    if not src or "/thumb/" not in src:
        return src
    before, after = src.split("/thumb/", 1)
    directory, _, _thumb_filename = after.rpartition("/")
    return f"{before}/{directory}" if directory else src


def absolute_url(url):
    """MediaWiki image URLs are protocol-relative ("//host/path"); make them fetchable."""
    return f"https:{url}" if url.startswith("//") else url


def fetch_page(url):
    print(f"Fetching {url} ...")
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    return resp.text


def parse_event_units(html):
    parser = GachaUnitParser()
    parser.feed(html)
    return parser.units


def units_to_csv(units):
    buf = io.StringIO()
    # extrasaction="ignore": unit dicts also carry image_url (used only for icon
    # downloads), which isn't one of the CSV columns.
    writer = csv.DictWriter(buf, fieldnames=UNIT_CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(units)
    return buf.getvalue()


def sanitize_name(name, fallback="event"):
    """Make a user-provided name safe to use as a Windows folder/file name."""
    name = INVALID_NAME_CHARS_RE.sub("_", name.strip())
    return name.rstrip(" .") or fallback


def icon_path(name, image_url):
    """Where save_icon() would write this unit's icon. Reads the module-level ICONS_DIR
    at call time (rather than as a default argument) so tests can point it elsewhere."""
    suffix = Path(urlparse(absolute_url(image_url)).path).suffix or ".png"
    return ICONS_DIR / f"{sanitize_name(name, fallback='unit')}{suffix}"


def save_icon(name, image_url):
    """Download one unit's icon to ICONS_DIR/<name>.<ext> unless it's already there.
    Returns "saved", "cached", or "no-image" (image_url was blank); raises
    requests.RequestException on a failed download, same as fetch_page."""
    if not image_url:
        return "no-image"
    path = icon_path(name, image_url)
    if path.exists():
        return "cached"
    resp = requests.get(absolute_url(image_url), headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(resp.content)
    return "saved"


def main():
    ap = argparse.ArgumentParser(
        description="Fetch a Battle Cats wiki gacha drop page's event units into CSV(s)."
    )
    ap.add_argument("urls", nargs="+", help="one or more battlecats.miraheze.org gacha drop page URLs")
    ap.add_argument("event_name", help='event name, e.g. "Fate Stay Night"')
    args = ap.parse_args()

    event_name = sanitize_name(args.event_name)
    out_dir = GACHA_POOLS_DIR / event_name

    for i, url in enumerate(args.urls, start=1):
        try:
            html = fetch_page(url)
        except requests.RequestException as e:
            sys.exit(f"Could not fetch {url}: {e}")

        units = parse_event_units(html)
        if not units:
            sys.exit(f"Parsed 0 event units from {url}; the page layout may have changed, "
                      f"or this isn't a gacha drop page.")

        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{event_name}_units_{i}.csv"
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            f.write(units_to_csv(units))
        print(f"Wrote {len(units)} units to {out_path}")

        saved = cached = failed = 0
        for unit in units:
            try:
                status = save_icon(unit["name"], unit.get("image_url", ""))
            except requests.RequestException as e:
                failed += 1
                print(f"  Could not fetch icon for {unit['name']}: {e}")
                continue
            saved += status == "saved"
            cached += status == "cached"
        if saved or cached or failed:
            print(f"Icons: {saved} downloaded, {cached} already saved, {failed} failed -> {ICONS_DIR}")


if __name__ == "__main__":
    main()
