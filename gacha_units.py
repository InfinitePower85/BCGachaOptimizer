"""
Read the event unit rosters written by fetch_gacha_units.py -- data/gacha_pools/<event>/
<event>_units_<n>.csv, one row per unit (rarity, name, description, cat_id) -- for the
server's /gacha-events and /gacha-units routes.

These are the wiki's unit rosters (which cats exist in an event's pool), not a seed's
rolled tracks. See fetch_gacha_units.py for how they're produced, and data_download.py /
route_optimizer.py for the seed-tracks side the frontend's optimizer runs against.
"""

import csv
import re
from pathlib import Path

from fetch_gacha_units import ICONS_DIR, sanitize_name

GACHA_POOLS_DIR = Path(__file__).parent / "data" / "gacha_pools"

# Event folder names are user-supplied (see fetch_gacha_units.py's sanitize_name) and
# end up as part of a filesystem path here, so they're checked against an allowlist
# pattern before ever touching disk, rather than just filtered/escaped.
EVENT_NAME_RE = re.compile(r"^[A-Za-z0-9 _-]+$")


def list_gacha_events():
    """Names of event folders under data/gacha_pools that have at least one units CSV,
    sorted alphabetically. An event with no CSV yet (not fetched) is left out rather
    than shown with an empty checkbox list."""
    if not GACHA_POOLS_DIR.is_dir():
        return []
    return sorted(
        p.name for p in GACHA_POOLS_DIR.iterdir()
        if p.is_dir() and any(p.glob("*_units_*.csv"))
    )


def _event_dir(event):
    """The folder for `event`, or raise ValueError if it isn't one of list_gacha_events()'s
    folders. Rejects path separators/traversal outright rather than trying to sanitize them."""
    if not EVENT_NAME_RE.fullmatch(event or ""):
        raise ValueError(f"Unknown event: {event!r}")
    event_dir = GACHA_POOLS_DIR / event
    if not event_dir.is_dir() or event_dir.resolve().parent != GACHA_POOLS_DIR.resolve():
        raise ValueError(f"Unknown event: {event!r}")
    return event_dir


def _icon_index():
    """Map a unit's sanitized name (fetch_gacha_units.py's icon_path() stem) to the icon
    filename actually saved for it, for whatever's currently in ICONS_DIR. Built once per
    load_gacha_units() call rather than searching the directory per unit."""
    if not ICONS_DIR.is_dir():
        return {}
    return {p.stem: p.name for p in ICONS_DIR.iterdir() if p.is_file()}


def load_gacha_units(event):
    """Merge every '*_units_*.csv' for one event (an event may have more than one gacha
    banner, e.g. two pools). Units are deduplicated by name, keeping the first
    occurrence, so a unit shared by two banners is only listed once."""
    event_dir = _event_dir(event)
    icons = _icon_index()
    seen = set()
    units = []
    for csv_path in sorted(event_dir.glob("*_units_*.csv")):
        with open(csv_path, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                name = (row.get("name") or "").strip()
                if not name or name in seen:
                    continue
                seen.add(name)
                units.append({
                    "rarity": (row.get("rarity") or "").strip(),
                    "name": name,
                    "description": (row.get("description") or "").strip(),
                    "cat_id": (row.get("cat_id") or "").strip(),
                    "icon": icons.get(sanitize_name(name, fallback="unit")),
                })
    return units


def group_by_rarity(units):
    """Group units by rarity, preserving the order rarities first appear in `units`
    (fetch_gacha_units.py writes the source page's own high-to-low tier order), with
    unit names sorted alphabetically within each group."""
    groups = {}
    for unit in units:
        groups.setdefault(unit["rarity"], []).append(unit)
    return [
        {"rarity": rarity, "units": sorted(items, key=lambda u: u["name"].casefold())}
        for rarity, items in groups.items()
    ]


def suggest_event(banner_text):
    """Best-effort guess at which known event a Godfat banner's display text is for.

    There's no real link between a Godfat event id (e.g. "2026-09-28_1081") and a wiki
    event name (e.g. "Fate Stay Night") -- the id is just a date plus an arbitrary
    internal number. But Godfat's banner text sometimes names a featured uber by its
    exact in-game name (e.g. "...heroes Shirou Emiya and True Assassin!..."), which we
    can cross-reference against known events' own unit rosters.

    Returns the event name if exactly one known event has a unit whose name appears (as
    a whole word) in banner_text, else None -- including when more than one event
    matches, since a pre-selection would then be as likely wrong as right. Meant only to
    pre-select the frontend's event dropdown; the user can always change it."""
    if not banner_text:
        return None
    matches = set()
    for event in list_gacha_events():
        for unit in load_gacha_units(event):
            if unit["name"] and re.search(rf"\b{re.escape(unit['name'])}\b", banner_text):
                matches.add(event)
                break
    return matches.pop() if len(matches) == 1 else None
