"""
Tests for gacha_units.py. No network access -- everything reads from tmp_path,
never the real data/gacha_pools.
"""

import pytest

import gacha_units
from gacha_units import group_by_rarity, list_gacha_events, load_gacha_units, suggest_event

UNIT_CSV_HEADER = "rarity,name,description,cat_id\n"


@pytest.fixture(autouse=True)
def pools_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(gacha_units, "GACHA_POOLS_DIR", tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def icons_dir(monkeypatch, tmp_path):
    """Icon lookups read this instead of the real data/icons/unit_icons."""
    monkeypatch.setattr(gacha_units, "ICONS_DIR", tmp_path / "icons")
    return tmp_path / "icons"


def write_units_csv(pools_dir, event, suffix, rows):
    event_dir = pools_dir / event
    event_dir.mkdir(parents=True, exist_ok=True)
    text = UNIT_CSV_HEADER + "".join(f'{r["rarity"]},{r["name"]},{r["description"]},{r["cat_id"]}\n' for r in rows)
    (event_dir / f"{event}_units_{suffix}.csv").write_text(text, encoding="utf-8")


# ---------- list_gacha_events ----------

def test_lists_only_folders_with_a_units_csv(pools_dir):
    write_units_csv(pools_dir, "Fate Stay Night", 1, [{"rarity": "Rare", "name": "A", "description": "", "cat_id": ""}])
    (pools_dir / "Street Fighters").mkdir()  # no csv yet
    assert list_gacha_events() == ["Fate Stay Night"]


def test_sorted_alphabetically(pools_dir):
    write_units_csv(pools_dir, "Zeta Event", 1, [{"rarity": "Rare", "name": "A", "description": "", "cat_id": ""}])
    write_units_csv(pools_dir, "Alpha Event", 1, [{"rarity": "Rare", "name": "A", "description": "", "cat_id": ""}])
    assert list_gacha_events() == ["Alpha Event", "Zeta Event"]


def test_missing_pools_dir_returns_empty(pools_dir, monkeypatch):
    monkeypatch.setattr(gacha_units, "GACHA_POOLS_DIR", pools_dir / "does-not-exist")
    assert list_gacha_events() == []


def test_a_non_matching_csv_name_does_not_count(pools_dir):
    event_dir = pools_dir / "Fate Stay Night"
    event_dir.mkdir()
    (event_dir / "notes.csv").write_text(UNIT_CSV_HEADER, encoding="utf-8")
    assert list_gacha_events() == []


# ---------- load_gacha_units ----------

def test_loads_rows_from_a_single_csv(pools_dir):
    write_units_csv(pools_dir, "Fate Stay Night", 1, [
        {"rarity": "Uber Super Rare", "name": "Saber", "description": "desc", "cat_id": "362"},
    ])
    units = load_gacha_units("Fate Stay Night")
    assert units == [{
        "rarity": "Uber Super Rare", "name": "Saber", "description": "desc", "cat_id": "362", "icon": None,
    }]


def test_merges_multiple_banners(pools_dir):
    write_units_csv(pools_dir, "Two Pools", 1, [{"rarity": "Rare", "name": "A", "description": "", "cat_id": ""}])
    write_units_csv(pools_dir, "Two Pools", 2, [{"rarity": "Rare", "name": "B", "description": "", "cat_id": ""}])
    names = {u["name"] for u in load_gacha_units("Two Pools")}
    assert names == {"A", "B"}


def test_a_unit_repeated_across_banners_is_not_duplicated(pools_dir):
    write_units_csv(pools_dir, "Two Pools", 1, [{"rarity": "Rare", "name": "Shared", "description": "first", "cat_id": "1"}])
    write_units_csv(pools_dir, "Two Pools", 2, [{"rarity": "Rare", "name": "Shared", "description": "second", "cat_id": "1"}])
    units = load_gacha_units("Two Pools")
    assert len(units) == 1
    assert units[0]["description"] == "first"   # first occurrence wins


def test_blank_name_rows_are_skipped(pools_dir):
    write_units_csv(pools_dir, "Fate Stay Night", 1, [{"rarity": "Rare", "name": "", "description": "", "cat_id": ""}])
    assert load_gacha_units("Fate Stay Night") == []


@pytest.mark.parametrize("event", ["../etc", "a/b", "a\\b", "", "Unknown Event", "x;y"])
def test_unknown_or_unsafe_event_raises(pools_dir, event):
    with pytest.raises(ValueError, match="Unknown event"):
        load_gacha_units(event)


def test_event_dir_must_be_a_direct_child(pools_dir):
    # a folder that exists but isn't directly under GACHA_POOLS_DIR must still be rejected
    nested = pools_dir / "sub" / "Fate Stay Night"
    nested.mkdir(parents=True)
    (nested / "Fate Stay Night_units_1.csv").write_text(UNIT_CSV_HEADER, encoding="utf-8")
    with pytest.raises(ValueError, match="Unknown event"):
        load_gacha_units("sub/Fate Stay Night")


# ---------- load_gacha_units: icon lookup ----------

def test_icon_filename_attached_when_present(pools_dir, icons_dir):
    write_units_csv(pools_dir, "Fate Stay Night", 1, [
        {"rarity": "Uber Super Rare", "name": "Saber", "description": "", "cat_id": "362"},
    ])
    icons_dir.mkdir(parents=True)
    (icons_dir / "Saber.png").write_bytes(b"fake")
    unit = load_gacha_units("Fate Stay Night")[0]
    assert unit["icon"] == "Saber.png"


def test_icon_is_none_when_not_downloaded(pools_dir, icons_dir):
    write_units_csv(pools_dir, "Fate Stay Night", 1, [
        {"rarity": "Rare", "name": "Saber", "description": "", "cat_id": "362"},
    ])
    unit = load_gacha_units("Fate Stay Night")[0]
    assert unit["icon"] is None


def test_icon_lookup_uses_the_same_sanitizing_as_fetch_gacha_units(pools_dir, icons_dir):
    # fetch_gacha_units.py saves e.g. "Kotomine & Gilgamesh Cats.png" as-is (no illegal
    # Windows filename characters in that name), so the lookup must match it exactly.
    write_units_csv(pools_dir, "Fate Stay Night", 1, [
        {"rarity": "Super Rare", "name": "Kotomine & Gilgamesh Cats", "description": "", "cat_id": "460"},
    ])
    icons_dir.mkdir(parents=True)
    (icons_dir / "Kotomine & Gilgamesh Cats.png").write_bytes(b"fake")
    unit = load_gacha_units("Fate Stay Night")[0]
    assert unit["icon"] == "Kotomine & Gilgamesh Cats.png"


def test_icon_lookup_does_not_care_about_extension(pools_dir, icons_dir):
    write_units_csv(pools_dir, "Fate Stay Night", 1, [
        {"rarity": "Rare", "name": "Saber", "description": "", "cat_id": "362"},
    ])
    icons_dir.mkdir(parents=True)
    (icons_dir / "Saber.gif").write_bytes(b"fake")
    unit = load_gacha_units("Fate Stay Night")[0]
    assert unit["icon"] == "Saber.gif"


def test_missing_icons_dir_gives_no_icons(pools_dir, icons_dir):
    write_units_csv(pools_dir, "Fate Stay Night", 1, [
        {"rarity": "Rare", "name": "Saber", "description": "", "cat_id": "362"},
    ])
    assert not icons_dir.exists()
    unit = load_gacha_units("Fate Stay Night")[0]
    assert unit["icon"] is None


# ---------- group_by_rarity ----------

def test_groups_and_sorts_names_alphabetically_within_group():
    units = [
        {"rarity": "Rare", "name": "Zed", "description": "", "cat_id": ""},
        {"rarity": "Rare", "name": "Amy", "description": "", "cat_id": ""},
        {"rarity": "Uber Super Rare", "name": "Bob", "description": "", "cat_id": ""},
    ]
    groups = group_by_rarity(units)
    assert [g["rarity"] for g in groups] == ["Rare", "Uber Super Rare"]
    assert [u["name"] for u in groups[0]["units"]] == ["Amy", "Zed"]


def test_group_order_matches_first_appearance():
    units = [
        {"rarity": "Uber Super Rare", "name": "A", "description": "", "cat_id": ""},
        {"rarity": "Super Rare", "name": "B", "description": "", "cat_id": ""},
        {"rarity": "Rare", "name": "C", "description": "", "cat_id": ""},
    ]
    assert [g["rarity"] for g in group_by_rarity(units)] == ["Uber Super Rare", "Super Rare", "Rare"]


def test_alphabetical_sort_is_case_insensitive():
    units = [
        {"rarity": "Rare", "name": "bob", "description": "", "cat_id": ""},
        {"rarity": "Rare", "name": "Amy", "description": "", "cat_id": ""},
    ]
    assert [u["name"] for u in group_by_rarity(units)[0]["units"]] == ["Amy", "bob"]


def test_empty_units_list_gives_empty_groups():
    assert group_by_rarity([]) == []


# ---------- suggest_event ----------

def test_suggest_event_matches_on_a_named_unit(pools_dir):
    write_units_csv(pools_dir, "Fate Stay Night", 1, [
        {"rarity": "Uber Super Rare", "name": "Shirou Emiya", "description": "", "cat_id": "864"},
    ])
    banner = "2026-09-28 ~ 2026-10-05: NEW Uber Rare heroes Shirou Emiya and True Assassin!"
    assert suggest_event(banner) == "Fate Stay Night"


def test_suggest_event_none_when_no_unit_named(pools_dir):
    write_units_csv(pools_dir, "Fate Stay Night", 1, [
        {"rarity": "Uber Super Rare", "name": "Shirou Emiya", "description": "", "cat_id": "864"},
    ])
    assert suggest_event("2026-03-23 ~ 2026-03-27: Zombie Outbreak Warning!") is None


def test_suggest_event_none_for_blank_banner(pools_dir):
    write_units_csv(pools_dir, "Fate Stay Night", 1, [
        {"rarity": "Uber Super Rare", "name": "Shirou Emiya", "description": "", "cat_id": "864"},
    ])
    assert suggest_event("") is None
    assert suggest_event(None) is None


def test_suggest_event_none_when_two_events_both_match(pools_dir):
    write_units_csv(pools_dir, "Event A", 1, [{"rarity": "Rare", "name": "Ren", "description": "", "cat_id": ""}])
    write_units_csv(pools_dir, "Event B", 1, [{"rarity": "Rare", "name": "Ren", "description": "", "cat_id": ""}])
    assert suggest_event("Featuring Ren!") is None


def test_suggest_event_matches_whole_words_only(pools_dir):
    # "Rin" must not match inside an unrelated word like "Marina"
    write_units_csv(pools_dir, "Fate Stay Night", 1, [{"rarity": "Rare", "name": "Rin", "description": "", "cat_id": ""}])
    assert suggest_event("Marina's big debut!") is None


def test_suggest_event_ignores_events_with_no_units_in_the_banner(pools_dir):
    write_units_csv(pools_dir, "Fate Stay Night", 1, [{"rarity": "Uber Super Rare", "name": "Saber", "description": "", "cat_id": "362"}])
    write_units_csv(pools_dir, "Unrelated Event", 1, [{"rarity": "Rare", "name": "Nope", "description": "", "cat_id": ""}])
    assert suggest_event("New hero: Saber joins the fray!") == "Fate Stay Night"
