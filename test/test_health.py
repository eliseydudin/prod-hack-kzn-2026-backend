from test.client import client


def test_health():
    result = client.get("/health")
    assert result.status_code == 200
    assert result.json() == {"status": "ok"}
