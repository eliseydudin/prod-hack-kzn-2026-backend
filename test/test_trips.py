from test.client import client


def test_trips_require_authentication():
    assert client.get("/api/v1/trips").status_code == 401
