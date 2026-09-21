from fastapi.testclient import TestClient

from server import app

client = TestClient(app)


def test_hello_world():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == "Hello World"
