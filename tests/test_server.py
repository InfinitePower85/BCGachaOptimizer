import pytest
from fastapi.testclient import TestClient

from server import MAX_MEOWS, app

client = TestClient(app)

PAGES_ORIGIN = "https://infinitepower85.github.io"


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
