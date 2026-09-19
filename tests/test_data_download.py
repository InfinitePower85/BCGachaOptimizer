"""
Tests for data_download.py.

No network access: requests.get is replaced with a function that fails the test
if it is ever called, and the parser runs on a small saved page in
tests/fixtures/mini_tracks.html.
"""

import csv
import json
import os
import re
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


@pytest.fixture(autouse=True)
def seed_dir(monkeypatch, tmp_path):
    """Every test writes to a temp folder, never to the real data/seed_tracks."""
    monkeypatch.setattr(data_download, "SEED_TRACKS_DIR", tmp_path)
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

            def raise_for_status(self):
                if web.fail:
                    raise RuntimeError("HTTP 500")

        return Response()


@pytest.fixture
def fake_web(monkeypatch, html):
    web = FakeWeb(html)
    monkeypatch.setattr(data_download.requests, "get", web)
    return web


def write_cache(url, text):
    path = data_download.cache_path(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def seed_log(times, append=False):
    log = data_download.fetch_log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a" if append else "w", encoding="utf-8") as f:
        for t in times:
            f.write(json.dumps({"t": t, "url": "x"}) + "\n")


def read_log():
    log = data_download.fetch_log_path()
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


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


# ---------- event id safety ----------

@pytest.mark.parametrize("event", ["../x", "a/b", "a\\b", "a b", "..", "x;y", "%2e%2e"])
def test_rejects_unsafe_event_ids(event):
    with pytest.raises(ValueError, match="unexpected characters"):
        validate_url(f"https://bc.godfat.org/?seed=1&event={event}")


# ---------- URL normalization and cache keys ----------

def test_normalize_ignores_order_scheme_and_irrelevant_params():
    a = "https://bc.godfat.org/?seed=1&event=e_1&ui=en&last=5&count=120"
    b = "http://bc.godfat.org/?count=120&last=5&ui=en&event=e_1&seed=1&pick=2AX&pos=3B"
    assert data_download.normalize_url(a) == data_download.normalize_url(b)
    assert data_download.cache_path(a) == data_download.cache_path(b)


@pytest.mark.parametrize("changed", [
    "seed=2&event=e_1&ui=en&last=5&count=120",     # different seed
    "seed=1&event=e_2&ui=en&last=5&count=120",     # different banner
    "seed=1&event=e_1&ui=tw&last=5&count=120",     # different language
    "seed=1&event=e_1&ui=en&last=6&count=120",     # different last cat
    "seed=1&event=e_1&ui=en&last=5&count=60",      # different row count
])
def test_content_params_change_the_cache_key(changed):
    base = "https://bc.godfat.org/?seed=1&event=e_1&ui=en&last=5&count=120"
    assert data_download.cache_path(base) != data_download.cache_path("https://bc.godfat.org/?" + changed)


def test_cache_files_live_under_dot_cache(seed_dir):
    path = data_download.cache_path(GOOD_URL)
    assert path.parent == seed_dir / ".cache"
    assert path.suffix == ".html"


# ---------- download(): caching (no network) ----------

def test_fresh_cache_is_used_without_a_request_or_log_entry(seed_dir, html):
    write_cache(GOOD_URL, html)
    assert data_download.download(GOOD_URL) == html   # the autouse boom() would fail the test
    assert read_log() == []


def test_reordered_url_hits_the_same_cache(seed_dir, html):
    write_cache(GOOD_URL, html)
    reordered = "https://bc.godfat.org/?event=2026-09-28_1081&seed=1234567890&last=523&ui=en&count=120&pick=9A"
    assert data_download.download(reordered) == html


def test_a_different_url_is_not_served_from_another_urls_cache(seed_dir, html, fake_web):
    write_cache(GOOD_URL, html)
    other = GOOD_URL.replace("2026-09-28_1081", "2026-09-30_947")
    data_download.download(other)
    assert len(fake_web.calls) == 1        # different gacha: no waiting on the first one's cache


def test_stale_cache_triggers_a_request(seed_dir, html, fake_web):
    raw = write_cache(GOOD_URL, html)
    old = time.time() - data_download.CACHE_MAX_AGE - 60
    os.utime(raw, (old, old))
    data_download.download(GOOD_URL)
    assert len(fake_web.calls) == 1


def test_cache_younger_than_a_day_is_still_fresh(seed_dir, html):
    raw = write_cache(GOOD_URL, html)
    almost = time.time() - data_download.CACHE_MAX_AGE + 300
    os.utime(raw, (almost, almost))
    assert data_download.download(GOOD_URL) == html


def test_force_bypasses_a_fresh_cache(seed_dir, html, fake_web):
    write_cache(GOOD_URL, html)
    data_download.download(GOOD_URL, force=True)
    assert len(fake_web.calls) == 1


def test_real_request_saves_cache_and_sends_user_agent(seed_dir, html, fake_web):
    assert data_download.download(GOOD_URL) == html
    assert data_download.cache_path(GOOD_URL).read_text(encoding="utf-8") == html
    assert len(fake_web.calls) == 1
    assert fake_web.calls[0][1]["headers"]["User-Agent"] == data_download.USER_AGENT
    assert data_download.USER_AGENT.strip()


# ---------- download(): hourly cap ----------

def test_real_request_is_logged(seed_dir, fake_web):
    data_download.download(GOOD_URL)
    log = read_log()
    assert len(log) == 1
    assert log[0]["url"] == data_download.normalize_url(GOOD_URL)
    assert abs(log[0]["t"] - time.time()) < 5


def test_tenth_request_is_allowed_and_eleventh_is_refused(seed_dir, fake_web):
    seed_log([time.time() - 60] * 9)
    data_download.download(GOOD_URL)                                   # 10th in the window: allowed
    with pytest.raises(data_download.RateLimitError, match="Hourly limit"):
        data_download.download(GOOD_URL.replace("1081", "999"))        # 11th, a new URL: refused
    assert len(fake_web.calls) == 1


def test_cap_blocks_a_new_real_request(seed_dir, fake_web):
    seed_log([time.time() - 60] * 10)
    with pytest.raises(data_download.RateLimitError, match="Hourly limit"):
        data_download.download(GOOD_URL)
    assert fake_web.calls == []


def test_refusal_does_not_log_anything(seed_dir, fake_web):
    seed_log([time.time() - 60] * 10)
    with pytest.raises(data_download.RateLimitError):
        data_download.download(GOOD_URL)
    assert len(read_log()) == 10


def test_cache_hits_are_free_even_at_the_cap(seed_dir, html):
    seed_log([time.time() - 60] * 10)
    write_cache(GOOD_URL, html)
    assert data_download.download(GOOD_URL) == html
    assert len(read_log()) == 10


def test_requests_older_than_an_hour_do_not_count(seed_dir, fake_web):
    seed_log([time.time() - data_download.RATE_WINDOW - 5] * 10)
    data_download.download(GOOD_URL)
    assert len(fake_web.calls) == 1


def test_window_is_rolling_not_fixed(seed_dir, fake_web):
    now = time.time()
    # 9 old ones just outside the window, 1 recent: only 1 counts
    seed_log([now - data_download.RATE_WINDOW - 1] * 9 + [now - 10])
    data_download.download(GOOD_URL)
    assert len(fake_web.calls) == 1


def test_refusal_message_says_roughly_how_long_to_wait(seed_dir):
    now = time.time()
    seed_log([now - data_download.RATE_WINDOW + 600] + [now - 5] * 9)   # oldest expires in ~10 min
    with pytest.raises(data_download.RateLimitError) as err:
        data_download.download(GOOD_URL)
    minutes = int(re.search(r"about (\d+) minute", str(err.value)).group(1))
    assert 10 <= minutes <= 11


def test_force_bypasses_the_cap_but_is_still_logged(seed_dir, fake_web):
    seed_log([time.time() - 60] * 10)
    data_download.download(GOOD_URL, force=True)
    assert len(fake_web.calls) == 1
    assert len(read_log()) == 11
    # ...and the forced request counts toward the cap for normal runs
    with pytest.raises(data_download.RateLimitError):
        data_download.download(GOOD_URL.replace("1081", "999"))


def test_failed_request_still_counts(seed_dir, fake_web):
    fake_web.fail = True
    with pytest.raises(RuntimeError, match="HTTP 500"):
        data_download.download(GOOD_URL)
    assert len(read_log()) == 1
    assert not data_download.cache_path(GOOD_URL).exists()   # nothing cached from the failure


def test_damaged_log_lines_are_skipped(seed_dir, fake_web):
    log = data_download.fetch_log_path()
    log.write_text('not json\n{"no_t": 1}\n{"t": "text"}\n', encoding="utf-8")
    seed_log([time.time() - 60] * 3, append=True)
    assert len(data_download.recent_fetches(time.time())) == 3
    data_download.download(GOOD_URL)


def test_missing_log_means_no_recent_fetches(seed_dir):
    assert data_download.recent_fetches(time.time()) == []


# ---------- main() end to end (no network) ----------

def run_main(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["data_download.py", *argv])
    data_download.main()


def test_main_writes_csv_and_meta_under_seed_and_event(monkeypatch, seed_dir, html):
    write_cache(GOOD_URL, html)
    run_main(monkeypatch, GOOD_URL)

    out = seed_dir / "1234567890" / "2026-09-28_1081"
    with open(out / "tracks.csv", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 5
    assert rows[1]["link"] == "-> 11B"
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    assert meta["seed"] == "1234567890"
    assert meta["event"] == "2026-09-28_1081"
    assert meta["cells"] == 5
    assert "Test banner" in meta["banner"]
    assert read_log() == []                      # served from cache: not a real request


def test_two_events_for_one_seed_do_not_overwrite_each_other(monkeypatch, seed_dir, html, fake_web):
    other = GOOD_URL.replace("2026-09-28_1081", "2026-09-30_947")
    run_main(monkeypatch, GOOD_URL)
    run_main(monkeypatch, other)
    assert (seed_dir / "1234567890" / "2026-09-28_1081" / "tracks.csv").exists()
    assert (seed_dir / "1234567890" / "2026-09-30_947" / "tracks.csv").exists()
    assert len(fake_web.calls) == 2


def test_main_exits_with_message_when_rate_limited(monkeypatch, seed_dir, fake_web):
    seed_log([time.time() - 60] * 10)
    with pytest.raises(SystemExit, match="Hourly limit"):
        run_main(monkeypatch, GOOD_URL)
    assert fake_web.calls == []
    assert not (seed_dir / "1234567890").exists()


def test_main_force_flag_reaches_download(monkeypatch, seed_dir, html, fake_web):
    write_cache(GOOD_URL, html)
    run_main(monkeypatch, GOOD_URL, "--force")
    assert len(fake_web.calls) == 1


def test_main_exits_on_bad_link_without_touching_disk(monkeypatch, seed_dir):
    with pytest.raises(SystemExit, match="Bad link"):
        run_main(monkeypatch, "https://example.com/?seed=1&event=x")
    assert list(seed_dir.iterdir()) == []


def test_main_exits_when_page_has_no_cells(monkeypatch, seed_dir):
    write_cache(GOOD_URL, "<html>layout changed</html>")
    with pytest.raises(SystemExit, match="0 cells"):
        run_main(monkeypatch, GOOD_URL)
    assert not (seed_dir / "1234567890").exists()
