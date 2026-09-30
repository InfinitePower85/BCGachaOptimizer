"""
Read data/unit_data (a fan-wiki scrape of every unit currently in the game, built outside
this repo -- see query_units.py) for the server's /unit-rarities route: a name -> rarity
map the frontend's roll simulator uses to classify units in its "Collected" panel.

This is a unit's real, intrinsic rarity (Basic/Rare/Super Rare/Uber Super Rare/Legendary
Rare/Special), which is NOT the same thing as bc.godfat's own per-cell rarity class that
data_download.py / route_optimizer.py read (rare/supa/uber/legend and their _fest
variants): that one describes the pull SLOT a roll landed in, which can be a "legend_fest"
tier slot on a collab banner even when the unit that comes out of it is, by the game's own
classification, an "Uber Super Rare" (e.g. Illyasviel, cat_id 365) -- see docs/app.js's
LEGEND_RARITIES comment. The two datasets overlap (both ultimately describe a cat) but
answer different questions, so they're kept separate rather than reconciled into one.

Keyed by each unit's Normal form name, since that's what a gacha pull always yields --
matching fetch_gacha_units.py's "Only each unit's Normal form is kept" for the wiki gacha
roster, and in turn what data_download.py's cat_name always is.
"""

import json
from pathlib import Path

UNIT_DATA_DIR = Path(__file__).parent / "data" / "unit_data"
UNITS_DIR = UNIT_DATA_DIR / "units"


def load_unit_rarities() -> dict:
    """{name: rarity} for every unit's Normal form. Empty if the database isn't present --
    it's large (a few thousand files) and separately sourced, so not guaranteed to exist
    in every checkout."""
    index_path = UNIT_DATA_DIR / "index.json"
    if not index_path.is_file():
        return {}
    index = json.loads(index_path.read_text(encoding="utf-8"))

    rarities = {}
    for unit_id in index.get("by_id", {}):
        unit_path = UNITS_DIR / f"{unit_id}.json"
        if not unit_path.is_file():
            continue
        unit = json.loads(unit_path.read_text(encoding="utf-8"))
        normal = next((f for f in unit.get("forms", []) if f.get("form_no") == 1), None)
        if normal and normal.get("name"):
            rarities[normal["name"]] = normal.get("rarity", "")
    return rarities
