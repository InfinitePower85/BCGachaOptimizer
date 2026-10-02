import threading

import fakeredis
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import data_download
import rate_limit
from rate_limit import ClientIPMiddleware, MemoryStore, RedisStore, Rule
from server import app

client = TestClient(app)

PAGES_ORIGIN = "https://infinitepower85.github.io"


@pytest.fixture(autouse=True)
def fresh_rate_limits(monkeypatch):
    monkeypatch.setattr(rate_limit, "store", MemoryStore())


def ip_app(trusted_hops):
    """A mini app behind ClientIPMiddleware that echoes the client IP it sees, with one
    rate-limited route (group "tracks") to check counters follow that IP."""
    mini = FastAPI()
    mini.add_middleware(ClientIPMiddleware, trusted_hops=trusted_hops)

    @mini.get("/ip")
    def ip(request: Request):
        return request.client.host

    @mini.get("/limited", dependencies=[rate_limit.rate_limited("tracks")])
    def limited():
        return "ok"

    return TestClient(mini)


# ---------- ClientIPMiddleware ----------

@pytest.mark.parametrize("xff, expected", [
    ("1.2.3.4", "1.2.3.4"),                   # the entry Render's proxy added
    ("6.6.6.6, 1.2.3.4", "1.2.3.4"),          # a forged leftmost entry is ignored
    ("6.6.6.6,7.7.7.7 , 1.2.3.4", "1.2.3.4"),
])
def test_one_hop_takes_the_rightmost_entry(xff, expected):
    assert ip_app(1).get("/ip", headers={"X-Forwarded-For": xff}).json() == expected


def test_two_hops_takes_the_second_from_the_right():
    assert ip_app(2).get("/ip", headers={"X-Forwarded-For": "6.6.6.6, 1.2.3.4, 10.0.0.1"}).json() == "1.2.3.4"


def test_repeated_header_lines_are_read_in_order():
    headers = [("X-Forwarded-For", "6.6.6.6"), ("X-Forwarded-For", "1.2.3.4")]
    assert ip_app(1).get("/ip", headers=headers).json() == "1.2.3.4"


def test_without_the_header_the_connection_address_is_kept():
    assert ip_app(1).get("/ip").json() == "testclient"


def test_fewer_entries_than_hops_keeps_the_connection_address():
    assert ip_app(2).get("/ip", headers={"X-Forwarded-For": "6.6.6.6"}).json() == "testclient"


def test_zero_hops_ignores_the_header():
    assert ip_app(0).get("/ip", headers={"X-Forwarded-For": "1.2.3.4"}).json() == "testclient"


@pytest.mark.parametrize("env, expected", [
    ({}, 0),
    ({"RENDER": "true"}, 1),
    ({"RENDER": "true", "TRUSTED_PROXY_HOPS": "2"}, 2),
    ({"TRUSTED_PROXY_HOPS": "0", "RENDER": "true"}, 0),
])
def test_default_trusted_hops(monkeypatch, env, expected):
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("TRUSTED_PROXY_HOPS", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert rate_limit.default_trusted_hops() == expected


# ---------- per-IP limits ----------

def test_each_ip_has_its_own_counter(monkeypatch):
    monkeypatch.setitem(rate_limit.GROUPS, "tracks", [Rule(2, 60)])
    web = ip_app(1)
    for _ in range(2):
        assert web.get("/limited", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200
    assert web.get("/limited", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 429
    assert web.get("/limited", headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 200


def test_forging_the_header_does_not_reset_the_counter(monkeypatch):
    monkeypatch.setitem(rate_limit.GROUPS, "tracks", [Rule(1, 60)])
    web = ip_app(1)
    assert web.get("/limited", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200
    forged = web.get("/limited", headers={"X-Forwarded-For": "9.9.9.9, 1.1.1.1"})
    assert forged.status_code == 429


def test_ipv6_is_limited_per_64(monkeypatch):
    monkeypatch.setitem(rate_limit.GROUPS, "tracks", [Rule(1, 60)])
    web = ip_app(1)
    assert web.get("/limited", headers={"X-Forwarded-For": "2001:db8:1:2::1"}).status_code == 200
    assert web.get("/limited", headers={"X-Forwarded-For": "2001:db8:1:2::ffff"}).status_code == 429
    assert web.get("/limited", headers={"X-Forwarded-For": "2001:db8:1:3::1"}).status_code == 200


def test_429_has_retry_after_and_a_readable_detail(monkeypatch):
    monkeypatch.setitem(rate_limit.GROUPS, "low", [Rule(0, 300)])
    response = client.get("/gacha-events", headers={"Origin": PAGES_ORIGIN})
    assert response.status_code == 429
    assert 1 <= int(response.headers["Retry-After"]) <= 300
    assert response.json()["detail"].startswith("Too many requests.")
    # exposed via CORS, so the frontend could read it
    assert "retry-after" in response.headers["access-control-expose-headers"].lower()


@pytest.mark.parametrize("path, params", [
    ("/gacha-events", {}),
    ("/gacha-units", {"event": "x"}),
    ("/unit-rarities", {}),
    ("/collab-units", {}),
    ("/match-event", {"text": "x"}),
    ("/meow", {"n": 1}),
    ("/tracks", {"url": "not a url"}),
])
def test_every_api_route_is_limited(monkeypatch, path, params):
    for group in rate_limit.GROUPS:
        monkeypatch.setitem(rate_limit.GROUPS, group, [Rule(0, 60)])
    assert client.get(path, params=params).status_code == 429


def test_hello_world_is_not_limited(monkeypatch):
    for group in rate_limit.GROUPS:
        monkeypatch.setitem(rate_limit.GROUPS, group, [Rule(0, 60)])
    assert client.get("/").status_code == 200


def test_groups_count_separately(monkeypatch):
    monkeypatch.setitem(rate_limit.GROUPS, "tracks", [Rule(0, 60)])
    assert client.get("/gacha-events").status_code == 200


OPTIMIZE_BODY = {
    "csv": "position,roll,track,guaranteed,cat_id,cat_name,rarity,link\n1A,1,A,False,1,Cat,rare,\n",
    "target_units": ["Cat"],
    "max_rolls": 1,
}


def test_optimize_burst_rule_applies_alongside_the_window_rule(monkeypatch):
    monkeypatch.setitem(rate_limit.GROUPS, "optimize", [Rule(20, 900), Rule(2, 60)])
    for _ in range(2):
        assert client.post("/optimize", json=OPTIMIZE_BODY).status_code == 200
    assert client.post("/optimize", json=OPTIMIZE_BODY).status_code == 429


# ---------- /optimize concurrency ----------

def test_optimize_503_when_no_slot_is_free(monkeypatch):
    monkeypatch.setattr(rate_limit, "_optimize_slots", threading.BoundedSemaphore(1))
    rate_limit._optimize_slots.acquire()  # another request is running
    response = client.post("/optimize", json=OPTIMIZE_BODY)
    assert response.status_code == 503
    assert response.headers["Retry-After"] == str(rate_limit.OPTIMIZE_BUSY_RETRY_AFTER)


def test_optimize_releases_its_slot(monkeypatch):
    monkeypatch.setattr(rate_limit, "_optimize_slots", threading.BoundedSemaphore(1))
    assert client.post("/optimize", json=OPTIMIZE_BODY).status_code == 200
    assert client.post("/optimize", json={"csv": "x"}).status_code == 422  # failed runs release too
    assert client.post("/optimize", json=OPTIMIZE_BODY).status_code == 200


# ---------- stores ----------

def stores():
    return [MemoryStore(), RedisStore(fakeredis.FakeRedis())]


@pytest.mark.parametrize("store", stores(), ids=["memory", "redis"])
def test_incr_counts_per_key(store):
    assert [store.incr("a", 60, 1000) for _ in range(3)] == [1, 2, 3]
    assert store.incr("b", 60, 1000) == 1


def test_memory_incr_resets_after_ttl():
    store = MemoryStore()
    store.incr("a", 60, 1000)
    assert store.incr("a", 60, 1061) == 1


def test_redis_incr_sets_a_ttl():
    redis = fakeredis.FakeRedis()
    RedisStore(redis).incr("a", 60, 1000)
    assert 0 < redis.ttl("a") <= 60


@pytest.mark.parametrize("store", stores(), ids=["memory", "redis"])
def test_add_if_under_is_a_rolling_window(store):
    assert store.add_if_under("g", 2, 100, 1000)[0]
    assert store.add_if_under("g", 2, 100, 1050)[0]
    admitted, retry_after = store.add_if_under("g", 2, 100, 1060)
    assert not admitted
    assert retry_after == pytest.approx(40)  # the 1000 entry leaves the window at 1100
    assert store.add_if_under("g", 2, 100, 1101)[0]  # refusals don't take up a slot


class BrokenRedis:
    def __getattr__(self, name):
        def fail(*args, **kwargs):
            raise ConnectionError("redis is down")
        return fail


def test_redis_errors_fall_back_to_memory():
    store = RedisStore(BrokenRedis())
    assert [store.incr("a", 60, 1000) for _ in range(2)] == [1, 2]
    assert store.add_if_under("g", 1, 100, 1000)[0]
    assert not store.add_if_under("g", 1, 100, 1001)[0]


def test_make_store_is_memory_without_redis_url(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    assert isinstance(rate_limit.make_store(), MemoryStore)


def test_make_store_is_redis_with_redis_url(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")  # no connection is made yet
    assert isinstance(rate_limit.make_store(), RedisStore)


# ---------- shared Godfat cap ----------

def test_godfat_gate_refuses_past_the_shared_limit(monkeypatch):
    monkeypatch.setattr(rate_limit, "GODFAT_GLOBAL_LIMIT", 2)
    rate_limit.godfat_fetch_gate("u", 1000)
    rate_limit.godfat_fetch_gate("u", 1001)
    with pytest.raises(data_download.RateLimitError, match="site-wide"):
        rate_limit.godfat_fetch_gate("u", 1002)
