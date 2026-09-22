"""
Tests for fetch_gacha_units.py.

No network access: requests.get is replaced with a function that fails the test
if it is ever called, and the parser runs on a small saved page in
tests/fixtures/mini_gacha_drop.html.
"""

import csv
import io
import sys
from pathlib import Path

import pytest

import fetch_gacha_units
from fetch_gacha_units import parse_event_units, sanitize_name, units_to_csv

FIXTURE = Path(__file__).parent / "fixtures" / "mini_gacha_drop.html"
URL = "https://battlecats.miraheze.org/wiki/Some_Event/Gacha_Drop"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("Test tried to make a real HTTP request")

    monkeypatch.setattr(fetch_gacha_units.requests, "get", boom)


@pytest.fixture(autouse=True)
def pools_dir(monkeypatch, tmp_path):
    """Every test writes to a temp folder, never to the real data/gacha_pools."""
    monkeypatch.setattr(fetch_gacha_units, "GACHA_POOLS_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def html():
    return FIXTURE.read_text(encoding="utf-8")


class FakeWeb:
    """Stands in for requests.get: records calls, returns the fixture page (or fails)."""

    def __init__(self, html):
        self.html = html
        self.calls = []
        self.fail = False

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        web = self

        class Response:
            text = web.html
            encoding = "utf-8"

            def raise_for_status(self):
                if web.fail:
                    raise fetch_gacha_units.requests.exceptions.HTTPError("HTTP 500")

        return Response()


@pytest.fixture
def fake_web(monkeypatch, html):
    web = FakeWeb(html)
    monkeypatch.setattr(fetch_gacha_units.requests, "get", web)
    return web


# ---------- parse_event_units ----------

def test_only_event_units_are_collected(html):
    units = parse_event_units(html)
    names = [u["name"] for u in units]
    assert names == ["Saber", "Kotomine & Gilgamesh Cats"]


def test_units_outside_the_event_section_are_skipped(html):
    names = [u["name"] for u in parse_event_units(html)]
    assert "Decoy Cat" not in names


def test_regular_pool_units_in_the_event_table_are_skipped(html):
    names = [u["name"] for u in parse_event_units(html)]
    assert "Bodhisattva Cat" not in names
    assert "Delinquent Cat" not in names


def test_only_normal_form_is_kept(html):
    names = [u["name"] for u in parse_event_units(html)]
    assert "Saber CC" not in names


def test_rarity_is_the_preceding_heading_with_marker_stripped(html):
    units = parse_event_units(html)
    assert units[0]["rarity"] == "Uber Super Rare"
    assert units[1]["rarity"] == "Uber Super Rare"


def test_cat_id_parsed_from_image_filename(html):
    unit = parse_event_units(html)[0]
    assert unit["cat_id"] == "362"


def test_cat_id_blank_when_not_found(html):
    unit = parse_event_units(html)[1]
    assert unit["cat_id"] == ""


def test_description_joins_br_separated_lines(html):
    unit = parse_event_units(html)[0]
    assert unit["description"] == "Feared by the unjust for her strikes!"


def test_html_entities_are_decoded(html):
    unit = parse_event_units(html)[1]
    assert unit["name"] == "Kotomine & Gilgamesh Cats"
    assert "area attack & wide range" in unit["description"]


@pytest.mark.parametrize("html", ["", "<html></html>", "<h2 id=\"Event\">Event</h2><p>nothing here</p>"])
def test_page_without_units_parses_to_nothing(html):
    assert parse_event_units(html) == []


def test_truncated_html_does_not_crash(html):
    units = parse_event_units(html[: len(html) // 2])
    assert isinstance(units, list)


# ---------- units_to_csv ----------

def test_units_to_csv_round_trips(html):
    units = parse_event_units(html)
    rows = list(csv.DictReader(io.StringIO(units_to_csv(units))))
    assert [r["name"] for r in rows] == [u["name"] for u in units]
    assert list(rows[0].keys()) == ["rarity", "name", "description", "cat_id"]


# ---------- sanitize_name ----------

@pytest.mark.parametrize("raw,expected", [
    ("Fate Stay Night", "Fate Stay Night"),
    ("  Padded  ", "Padded"),
    ("Weird: Name?", "Weird_ Name_"),
    ("a/b\\c", "a_b_c"),
    ("trailing dot.", "trailing dot"),
    ("", "event"),
])
def test_sanitize_name(raw, expected):
    assert sanitize_name(raw) == expected


# ---------- main() end to end (no network) ----------

def run_main(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["fetch_gacha_units.py", *argv])
    fetch_gacha_units.main()


def test_main_writes_one_csv_per_url(monkeypatch, pools_dir, fake_web):
    run_main(monkeypatch, URL, URL, "Fate Stay Night")

    out_dir = pools_dir / "Fate Stay Night"
    csv1 = out_dir / "Fate Stay Night_units_1.csv"
    csv2 = out_dir / "Fate Stay Night_units_2.csv"
    assert csv1.exists() and csv2.exists()
    rows = list(csv.DictReader(csv1.open(encoding="utf-8")))
    assert len(rows) == 2
    assert len(fake_web.calls) == 2


def test_main_sanitizes_event_name_for_the_folder(monkeypatch, pools_dir, fake_web):
    run_main(monkeypatch, URL, "Weird: Name?")
    assert (pools_dir / "Weird_ Name_" / "Weird_ Name__units_1.csv").exists()


def test_main_exits_when_page_has_no_units(monkeypatch, pools_dir):
    class Web:
        def __call__(self, url, **kwargs):
            class Response:
                text = "<html>layout changed</html>"
                encoding = "utf-8"

                def raise_for_status(self):
                    pass

            return Response()

    monkeypatch.setattr(fetch_gacha_units.requests, "get", Web())
    with pytest.raises(SystemExit, match="0 event units"):
        run_main(monkeypatch, URL, "Fate Stay Night")
    assert not (pools_dir / "Fate Stay Night").exists()


def test_main_exits_on_request_failure(monkeypatch, pools_dir, fake_web):
    fake_web.fail = True
    with pytest.raises(SystemExit, match="Could not fetch"):
        run_main(monkeypatch, URL, "Fate Stay Night")
