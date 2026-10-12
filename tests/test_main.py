"""The app's own routes, in main.py."""


def test_health_check(client):
    """GET /health returns ok status."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
