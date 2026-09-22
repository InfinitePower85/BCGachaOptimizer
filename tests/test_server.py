from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import data_download
import gacha_units
from server import MAX_MEOWS, MAX_ROLL_LIMIT, MAX_TARGET_UNITS, app

client = TestClient(app)

PAGES_ORIGIN = "https://infinitepower85.github.io"
GOOD_URL = "https://bc.godfat.org/?seed=1234567890&event=2026-09-28_1081"
FIXTURE_HTML = (Path(__file__).parent / "fixtures" / "mini_tracks.html").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def seed_dir(monkeypatch, tmp_path):
    """/tracks writes through data_download's cache; keep it off the real data/ dir."""
    monkeypatch.setattr(data_download, "SEED_TRACKS_DIR", tmp_path)


@pytest.fixture(autouse=True)
def gacha_pools_dir(monkeypatch, tmp_path):
    """/gacha-events and /gacha-units read through gacha_units; keep it off data/gacha_pools."""
    pools_dir = tmp_path / "gacha_pools"
    monkeypatch.setattr(gacha_units, "GACHA_POOLS_DIR", pools_dir)
    return pools_dir


def write_units_csv(pools_dir, event, suffix, rows):
    event_dir = pools_dir / event
    event_dir.mkdir(parents=True, exist_ok=True)
    text = "rarity,name,description,cat_id\n" + "".join(
        f'{r["rarity"]},{r["name"]},{r["description"]},{r["cat_id"]}\n' for r in rows
    )
    (event_dir / f"{event}_units_{suffix}.csv").write_text(text, encoding="utf-8")


class FakeResponse:
    def __init__(self, text):
        self.text = text
        self.encoding = None

    def raise_for_status(self):
        pass


@pytest.fixture
def fake_web(monkeypatch):
    """Stands in for requests.get; records calls instead of hitting the network."""
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return FakeResponse(FIXTURE_HTML)

    monkeypatch.setattr(data_download.requests, "get", fake_get)
    return calls


def test_hello_world():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == "Hello World"


def test_meow_repeats_n_times():
    response = client.get("/meow", params={"n": 3})
    assert response.status_code == 200
    assert response.json() == "meow meow meow"


def test_meow_accepts_the_max():
    response = client.get("/meow", params={"n": MAX_MEOWS})
    assert response.status_code == 200
    assert response.json().count("meow") == MAX_MEOWS


@pytest.mark.parametrize("n", [0, -1, MAX_MEOWS + 1, 10**9, "abc", "1.5", ""])
def test_meow_rejects_bad_n(n):
    assert client.get("/meow", params={"n": n}).status_code == 422


def test_meow_requires_n():
    assert client.get("/meow").status_code == 422


@pytest.mark.parametrize("origin", [PAGES_ORIGIN, "http://localhost:8080", "http://127.0.0.1:5500"])
def test_cors_allows_pages_and_localhost(origin):
    response = client.get("/meow", params={"n": 1}, headers={"Origin": origin})
    assert response.headers["access-control-allow-origin"] == origin


@pytest.mark.parametrize("origin", ["https://evil.example", "http://localhost.evil.example"])
def test_cors_blocks_other_origins(origin):
    response = client.get("/meow", params={"n": 1}, headers={"Origin": origin})
    assert "access-control-allow-origin" not in response.headers


# ---------- /tracks ----------

def test_tracks_returns_meta_and_csv(fake_web):
    response = client.get("/tracks", params={"url": GOOD_URL})
    assert response.status_code == 200
    body = response.json()
    assert fake_web == [GOOD_URL]  # exactly one real request; a repeat would hit the cache

    assert body["meta"]["seed"] == "1234567890"
    assert body["meta"]["event"] == "2026-09-28_1081"
    assert body["meta"]["banner"] == "2026-09-28 ~ 2026-10-05: Test banner ★ Tap!"
    assert body["meta"]["cells"] == 5

    rows = body["csv"].strip().splitlines()
    assert rows[0] == "position,roll,track,guaranteed,cat_id,cat_name,rarity,link"
    assert len(rows) == 1 + 5


def test_tracks_second_call_uses_the_cache(fake_web):
    client.get("/tracks", params={"url": GOOD_URL})
    client.get("/tracks", params={"url": GOOD_URL})
    assert fake_web == [GOOD_URL]  # only the first call hit the network


@pytest.mark.parametrize("url", [
    "https://example.com/?seed=1&event=x",   # wrong host
    "https://bc.godfat.org/?event=x",        # missing seed
    "not a url",
])
def test_tracks_rejects_bad_link(url, fake_web):
    response = client.get("/tracks", params={"url": url})
    assert response.status_code == 400
    assert fake_web == []  # never reached the network


def test_tracks_502_when_page_has_no_cells(monkeypatch, fake_web):
    monkeypatch.setattr(data_download.requests, "get", lambda url, **kw: FakeResponse("<html></html>"))
    response = client.get("/tracks", params={"url": GOOD_URL})
    assert response.status_code == 502


def test_tracks_429_when_hourly_cap_reached(monkeypatch, fake_web):
    monkeypatch.setattr(data_download, "MAX_FETCHES_PER_HOUR", 0)
    response = client.get("/tracks", params={"url": GOOD_URL})
    assert response.status_code == 429
    assert fake_web == []


# ---------- /gacha-events, /gacha-units ----------

def test_gacha_events_lists_folders_with_a_units_csv(gacha_pools_dir):
    write_units_csv(gacha_pools_dir, "Fate Stay Night", 1, [{"rarity": "Rare", "name": "A", "description": "", "cat_id": ""}])
    (gacha_pools_dir / "Street Fighters").mkdir(parents=True)  # not fetched yet: no csv
    response = client.get("/gacha-events")
    assert response.status_code == 200
    assert response.json() == ["Fate Stay Night"]


def test_gacha_events_empty_when_nothing_fetched(gacha_pools_dir):
    assert client.get("/gacha-events").json() == []


def test_gacha_units_grouped_by_rarity_and_sorted(gacha_pools_dir):
    write_units_csv(gacha_pools_dir, "Fate Stay Night", 1, [
        {"rarity": "Uber Super Rare", "name": "Zed", "description": "z desc", "cat_id": "9"},
        {"rarity": "Uber Super Rare", "name": "Amy", "description": "a desc", "cat_id": "1"},
        {"rarity": "Rare", "name": "Bob", "description": "b desc", "cat_id": ""},
    ])
    response = client.get("/gacha-units", params={"event": "Fate Stay Night"})
    assert response.status_code == 200
    body = response.json()
    assert body["event"] == "Fate Stay Night"
    assert [g["rarity"] for g in body["rarities"]] == ["Uber Super Rare", "Rare"]
    assert [u["name"] for u in body["rarities"][0]["units"]] == ["Amy", "Zed"]
    assert body["rarities"][0]["units"][0] == {"rarity": "Uber Super Rare", "name": "Amy", "description": "a desc", "cat_id": "1"}


def test_gacha_units_merges_two_banners_without_duplicates(gacha_pools_dir):
    write_units_csv(gacha_pools_dir, "Two Pools", 1, [{"rarity": "Rare", "name": "Shared", "description": "", "cat_id": ""}])
    write_units_csv(gacha_pools_dir, "Two Pools", 2, [{"rarity": "Rare", "name": "Shared", "description": "", "cat_id": ""}])
    body = client.get("/gacha-units", params={"event": "Two Pools"}).json()
    assert sum(len(g["units"]) for g in body["rarities"]) == 1


def test_gacha_units_404_for_unknown_event(gacha_pools_dir):
    response = client.get("/gacha-units", params={"event": "Nope"})
    assert response.status_code == 404


@pytest.mark.parametrize("event", ["../etc", "a/b", "a\\b"])
def test_gacha_units_404_for_unsafe_event(gacha_pools_dir, event):
    response = client.get("/gacha-units", params={"event": event})
    assert response.status_code == 404


@pytest.mark.parametrize("origin", [PAGES_ORIGIN, "http://localhost:8080"])
def test_gacha_events_cors_allows_pages_and_localhost(origin):
    response = client.get("/gacha-events", headers={"Origin": origin})
    assert response.headers["access-control-allow-origin"] == origin


# ---------- /optimize ----------

OPTIMIZE_CSV = (
    "position,roll,track,guaranteed,cat_id,cat_name,rarity,link\n"
    "1A,1,A,False,1,Junk,rare,\n"
    "2A,2,A,False,2,Target,rare,\n"
)


def test_optimize_returns_score_and_route():
    response = client.post("/optimize", json={
        "csv": OPTIMIZE_CSV, "target_units": ["Target"], "max_rolls": 3,
    })
    assert response.status_code == 200
    body = response.json()
    assert body["score"] == 1
    assert body["collected"] == ["Target"]
    assert body["route"] == [
        {"type": "single", "track": "A", "roll": 1, "units": ["Junk"]},
        {"type": "single", "track": "A", "roll": 2, "units": ["Target"]},
    ]


def test_optimize_defaults_start_track_and_roll():
    response = client.post("/optimize", json={
        "csv": OPTIMIZE_CSV, "target_units": ["Target"], "max_rolls": 3,
    })
    assert response.status_code == 200
    assert response.json()["route"][0]["track"] == "A"
    assert response.json()["route"][0]["roll"] == 1


def test_optimize_rejects_unparseable_csv():
    response = client.post("/optimize", json={
        "csv": "not,a,valid,tracks,csv", "target_units": ["Target"], "max_rolls": 3,
    })
    assert response.status_code == 400


@pytest.mark.parametrize("overrides", [
    {"max_rolls": 0},                              # below minimum
    {"max_rolls": MAX_ROLL_LIMIT + 1},              # above the cap
    {"target_units": []},                           # need at least one target
    {"target_units": [f"u{i}" for i in range(MAX_TARGET_UNITS + 1)]},  # too many targets
    {"csv": None},                                  # missing csv
])
def test_optimize_rejects_bad_input(overrides):
    payload = {"csv": OPTIMIZE_CSV, "target_units": ["Target"], "max_rolls": 3, **overrides}
    response = client.post("/optimize", json=payload)
    assert response.status_code == 422


@pytest.mark.parametrize("origin", [PAGES_ORIGIN, "http://localhost:8080"])
def test_optimize_cors_allows_pages_and_localhost(origin):
    response = client.post(
        "/optimize", json={"csv": OPTIMIZE_CSV, "target_units": ["Target"], "max_rolls": 3},
        headers={"Origin": origin},
    )
    assert response.headers["access-control-allow-origin"] == origin


def test_optimize_cors_blocks_other_origins():
    response = client.post(
        "/optimize", json={"csv": OPTIMIZE_CSV, "target_units": ["Target"], "max_rolls": 3},
        headers={"Origin": "https://evil.example"},
    )
    assert "access-control-allow-origin" not in response.headers
