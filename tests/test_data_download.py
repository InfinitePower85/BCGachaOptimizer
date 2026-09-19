"""
Tests for data_download.py.

No network access: requests.get is replaced with a function that fails the test
if it is ever called, and the parser runs on a small saved page in
tests/fixtures/mini_tracks.html.
"""

import csv
import json
import os
import sys
import time
from pathlib import Path

import pytest

import data_download
from data_download import parse_tracks, validate_url

FIXTURE = Path(__file__).parent / "fixtures" / "mini_tracks.html"
GOOD_URL = "https://bc.godfat.org/?seed=1234567890&last=523&event=2026-09-28_1081&ui=en&count=120"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("Test tried to make a real HTTP request")

    monkeypatch.setattr(data_download.requests, "get", boom)


@pytest.fixture
def html():
    return FIXTURE.read_text(encoding="utf-8")


# ---------- validate_url ----------

def test_valid_url_returns_query():
    query = validate_url(GOOD_URL)
    assert query["seed"] == ["1234567890"]
    assert query["event"] == ["2026-09-28_1081"]


def test_http_scheme_is_accepted():
    validate_url(GOOD_URL.replace("https://", "http://"))


def test_path_may_be_empty():
    validate_url("https://bc.godfat.org?seed=1&event=x")


@pytest.mark.parametrize("url", [
    "https://example.com/?seed=1&event=x",                  # wrong host
    "https://bc.godfat.org.evil.com/?seed=1&event=x",       # host suffix trick
    "https://evil.com/bc.godfat.org/?seed=1&event=x",       # host in path
    "https://bc.godfat.org@evil.com/?seed=1&event=x",       # userinfo trick
    "https://www.bc.godfat.org/?seed=1&event=x",            # different subdomain
    "https://bc-seek.godfat.org/?seed=1&event=x",           # sibling site
    "ftp://bc.godfat.org/?seed=1&event=x",                  # wrong scheme
    "file:///etc/passwd",
    "bc.godfat.org/?seed=1&event=x",                        # no scheme
    "not a url",
    "",
])
def test_rejects_wrong_host_or_scheme(url):
    with pytest.raises(ValueError, match="bc.godfat.org"):
        validate_url(url)


@pytest.mark.parametrize("path", ["/cats", "/cats/326", "/help", "/logs"])
def test_rejects_sub_pages(path):
    with pytest.raises(ValueError, match="main tracks page"):
        validate_url(f"https://bc.godfat.org{path}?seed=1&event=x")


@pytest.mark.parametrize("query", [
    "event=x",              # missing
    "seed=&event=x",        # blank values are dropped by parse_qs
    "seed=abc&event=x",     # not numeric
    "seed=-5&event=x",      # negative
    "seed=1.5&event=x",     # decimal
    "seed=12 34&event=x",   # embedded space
])
def test_rejects_bad_seed(query):
    with pytest.raises(ValueError, match="seed"):
        validate_url(f"https://bc.godfat.org/?{query}")


def test_rejects_missing_event():
    with pytest.raises(ValueError, match="event"):
        validate_url("https://bc.godfat.org/?seed=1")


@pytest.mark.parametrize("url", [
    "https://bc.godfat.org/?seed=1234567890",   # what the shell passes when '&' is unquoted
    "https://bc.godfat.org/?event=x",
])
def test_truncated_link_errors_suggest_quoting(url):
    with pytest.raises(ValueError, match="in quotes"):
        validate_url(url)


# ---------- parse_tracks ----------

def test_banner_name_is_selected_event_only(html):
    _, event_name = parse_tracks(html)
    # whitespace collapsed; not the other banner or the language option
    assert event_name == "2026-09-28 ~ 2026-10-05: Test banner ★ Tap!"


def test_only_cat_cells_are_kept(html):
    cells, _ = parse_tracks(html)
    # score cells (1AX, 1AGX, 1BX, 1BGX) are empty placeholders and must be skipped
    assert [c["position"] for c in cells] == ["1A", "1A", "1B", "1B", "2A"]


def test_single_roll_cell(html):
    cell = parse_tracks(html)[0][0]
    assert cell == {
        "position": "1A", "roll": 1, "track": "A", "guaranteed": False,
        "rarity": "rare", "cat_id": "326", "cat_name": "Welterweight Cat", "link": "",
    }


def test_guaranteed_cell_has_forward_link(html):
    cell = parse_tracks(html)[0][1]
    assert (cell["position"], cell["guaranteed"]) == ("1A", True)
    assert (cell["cat_name"], cell["cat_id"]) == ("Lancer", "367")
    assert cell["link"] == "-> 11B"


def test_guaranteed_cell_has_backward_link(html):
    cell = parse_tracks(html)[0][3]
    assert (cell["position"], cell["track"], cell["guaranteed"]) == ("1B", "B", True)
    assert cell["cat_name"] == "Gilgamesh"   # the "<- 12A" prefix is not part of the name
    assert cell["link"] == "<- 12A"


def test_name_is_the_first_link_not_the_paw_link(html):
    cells, _ = parse_tracks(html)
    assert all(c["cat_name"] and c["cat_name"] != "🐾" for c in cells)


def test_html_entities_are_decoded(html):
    cell = parse_tracks(html)[0][4]
    assert cell["cat_name"] == "Ben & Jerry's Cat"
    assert cell["cat_id"] == "99"


def test_multi_digit_roll_numbers():
    html = """<table><tr><td class="position cat pick minor_rare major_rare" onclick="pick('120B')">
        <span><a href="/?seed=1">Some Cat</a> <a href="/cats/7?seed=1">x</a></span></td></tr></table>"""
    cell = parse_tracks(html)[0][0]
    assert (cell["position"], cell["roll"], cell["track"]) == ("120B", 120, "B")


def test_cell_without_cat_id_link_is_kept_with_blank_id():
    html = """<table><tr><td class="position cat pick minor_rare major_rare" onclick="pick('3A')">
        <span><a href="/?seed=1">Mystery</a></span></td></tr></table>"""
    cell = parse_tracks(html)[0][0]
    assert (cell["cat_name"], cell["cat_id"]) == ("Mystery", "")


@pytest.mark.parametrize("html", ["", "<html></html>", "<table><tr><td>nothing</td></tr></table>"])
def test_page_without_cells_parses_to_nothing(html):
    assert parse_tracks(html) == ([], "")


def test_truncated_html_does_not_crash(html):
    cells, _ = parse_tracks(html[: len(html) // 2])
    assert isinstance(cells, list)


# ---------- download() cache behaviour (still no network) ----------

def test_fresh_cache_is_used_without_a_request(tmp_path, html):
    (tmp_path / "raw.html").write_text(html, encoding="utf-8")
    assert data_download.download(GOOD_URL, tmp_path) == html   # boom() would fail the test


def test_stale_cache_triggers_a_request(tmp_path, html):
    raw = tmp_path / "raw.html"
    raw.write_text(html, encoding="utf-8")
    old = time.time() - data_download.CACHE_MAX_AGE - 60
    os.utime(raw, (old, old))
    with pytest.raises(AssertionError, match="real HTTP request"):
        data_download.download(GOOD_URL, tmp_path)


def test_force_bypasses_a_fresh_cache(tmp_path, html):
    (tmp_path / "raw.html").write_text(html, encoding="utf-8")
    with pytest.raises(AssertionError, match="real HTTP request"):
        data_download.download(GOOD_URL, tmp_path, force=True)


def test_download_saves_response_using_fake_request(tmp_path, html, monkeypatch):
    class FakeResponse:
        text = html

        def raise_for_status(self):
            pass

    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse()

    monkeypatch.setattr(data_download.requests, "get", fake_get)
    out_dir = tmp_path / "new_event"
    assert data_download.download(GOOD_URL, out_dir) == html
    assert (out_dir / "raw.html").read_text(encoding="utf-8") == html
    assert len(calls) == 1
    assert calls[0][1]["headers"]["User-Agent"] == data_download.USER_AGENT
    assert data_download.USER_AGENT.strip()


# ---------- main() end to end, from cache ----------

def run_main(monkeypatch, tmp_path, *argv):
    monkeypatch.setattr(data_download, "SEED_TRACKS_DIR", tmp_path)
    monkeypatch.setattr(sys, "argv", ["data_download.py", *argv])
    data_download.main()


def test_main_writes_csv_and_meta(monkeypatch, tmp_path, html):
    out = tmp_path / "2026-09-28_1081"
    out.mkdir()
    (out / "raw.html").write_text(html, encoding="utf-8")

    run_main(monkeypatch, tmp_path, GOOD_URL)

    with open(out / "tracks.csv", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 5
    assert rows[1]["link"] == "-> 11B"
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    assert meta["seed"] == "1234567890"
    assert meta["event"] == "2026-09-28_1081"
    assert meta["cells"] == 5
    assert "Test banner" in meta["banner"]


def test_main_exits_on_bad_link_without_touching_disk(monkeypatch, tmp_path):
    with pytest.raises(SystemExit, match="Bad link"):
        run_main(monkeypatch, tmp_path, "https://example.com/?seed=1&event=x")
    assert list(tmp_path.iterdir()) == []


def test_main_exits_when_page_has_no_cells(monkeypatch, tmp_path):
    out = tmp_path / "2026-09-28_1081"
    out.mkdir()
    (out / "raw.html").write_text("<html>layout changed</html>", encoding="utf-8")
    with pytest.raises(SystemExit, match="0 cells"):
        run_main(monkeypatch, tmp_path, GOOD_URL)
    assert not (out / "tracks.csv").exists()
