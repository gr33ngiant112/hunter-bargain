"""Tests for item CRUD API endpoints."""

import pytest

from hunter_bargain.config import settings

# Control characters (Unicode category Cc): LF, CR, tab, NUL, ESC, DEL and the C1 CSI.
CONTROL_CHARACTERS = ["\n", "\r", "\t", "\x00", "\x1b", "\x7f", "\x9b"]


def test_create_item(client):
    """POST /api/v1/items/ creates an item and returns it."""
    payload = {
        "name": "iPhone 15 Pro",
        "keywords": "256GB black titanium",
        "target_price": 899.99,
        "notify_email": "user@example.com",
    }
    resp = client.post("/api/v1/items/", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "iPhone 15 Pro"
    assert data["keywords"] == "256GB black titanium"
    assert data["target_price"] == 899.99
    assert data["notify_email"] == "user@example.com"
    assert "id" in data
    assert "created_at" in data


def test_list_items(client):
    """GET /api/v1/items/ returns all items."""
    # Create two items
    client.post("/api/v1/items/", json={"name": "Item A", "notify_email": "a@x.com"})
    client.post("/api/v1/items/", json={"name": "Item B", "notify_email": "b@x.com"})

    resp = client.get("/api/v1/items/")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 2


def test_get_item(client):
    """GET /api/v1/items/{id} returns a single item."""
    create_resp = client.post("/api/v1/items/", json={"name": "Widget", "notify_email": "w@x.com"})
    item_id = create_resp.json()["id"]

    resp = client.get(f"/api/v1/items/{item_id}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Widget"


def test_get_item_not_found(client):
    """GET /api/v1/items/999 returns 404."""
    resp = client.get("/api/v1/items/999")
    assert resp.status_code == 404


def test_update_item(client):
    """PATCH /api/v1/items/{id} updates specified fields only."""
    create_resp = client.post(
        "/api/v1/items/",
        json={"name": "Old Name", "target_price": 100.0, "notify_email": "u@x.com"},
    )
    item_id = create_resp.json()["id"]

    resp = client.patch(f"/api/v1/items/{item_id}", json={"name": "New Name"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "New Name"
    assert data["target_price"] == 100.0  # Unchanged


def test_delete_item(client):
    """DELETE /api/v1/items/{id} removes the item."""
    create_resp = client.post(
        "/api/v1/items/", json={"name": "ToDelete", "notify_email": "d@x.com"}
    )
    item_id = create_resp.json()["id"]

    resp = client.delete(f"/api/v1/items/{item_id}")
    assert resp.status_code == 204

    # Verify it's gone
    resp = client.get(f"/api/v1/items/{item_id}")
    assert resp.status_code == 404


def test_delete_item_not_found(client):
    """DELETE /api/v1/items/999 returns 404."""
    resp = client.delete("/api/v1/items/999")
    assert resp.status_code == 404


def test_create_item_rejects_recipient_not_in_alert_recipients(client, monkeypatch):
    """POST with a notify_email outside ALERT_RECIPIENTS returns 422 and stores nothing."""
    monkeypatch.setattr(settings, "alert_recipients", ["owner@example.com"])

    resp = client.post("/api/v1/items/", json={"name": "Widget", "notify_email": "x@example.net"})
    assert resp.status_code == 422
    error = resp.json()["detail"][0]
    assert error["loc"] == ["body", "notify_email"]
    assert "not in ALERT_RECIPIENTS" in error["msg"]
    assert "owner@example.com" not in resp.text  # the error does not reveal the allowed list
    assert client.get("/api/v1/items/").json() == []

    # The same request with an allowed address succeeds; letter case is ignored.
    resp = client.post(
        "/api/v1/items/", json={"name": "Widget", "notify_email": "Owner@example.com"}
    )
    assert resp.status_code == 201


def test_update_item_rejects_recipient_not_in_alert_recipients(client, monkeypatch):
    """PATCH with a notify_email outside ALERT_RECIPIENTS returns 422 and keeps the old one."""
    monkeypatch.setattr(settings, "alert_recipients", ["owner@example.com"])
    create_resp = client.post(
        "/api/v1/items/", json={"name": "Widget", "notify_email": "owner@example.com"}
    )
    assert create_resp.status_code == 201
    item_id = create_resp.json()["id"]

    resp = client.patch(f"/api/v1/items/{item_id}", json={"notify_email": "x@example.net"})
    assert resp.status_code == 422
    assert resp.json()["detail"][0]["loc"] == ["body", "notify_email"]
    assert client.get(f"/api/v1/items/{item_id}").json()["notify_email"] == "owner@example.com"


def test_empty_alert_recipients_rejects_every_address(client, monkeypatch):
    """With ALERT_RECIPIENTS unset, no address is allowed."""
    monkeypatch.setattr(settings, "alert_recipients", [])

    resp = client.post(
        "/api/v1/items/", json={"name": "Widget", "notify_email": "user@example.com"}
    )
    assert resp.status_code == 422


@pytest.mark.parametrize("char", CONTROL_CHARACTERS)
@pytest.mark.parametrize("field", ["name", "keywords"])
def test_create_item_rejects_control_characters(client, field, char):
    """POST with a control character in name or keywords returns 422 and stores nothing."""
    payload = {"name": "Widget", "keywords": "blue", "notify_email": "user@example.com"}
    payload[field] = f"Widget{char}Pro"

    resp = client.post("/api/v1/items/", json=payload)

    assert resp.status_code == 422
    error = resp.json()["detail"][0]
    assert error["loc"] == ["body", field]
    assert "must not contain control characters" in error["msg"]
    assert client.get("/api/v1/items/").json() == []


@pytest.mark.parametrize("char", CONTROL_CHARACTERS)
@pytest.mark.parametrize("field", ["name", "keywords"])
def test_update_item_rejects_control_characters(client, field, char):
    """PATCH with a control character in name or keywords returns 422 and keeps the old text."""
    create_resp = client.post(
        "/api/v1/items/",
        json={"name": "Widget", "keywords": "blue", "notify_email": "user@example.com"},
    )
    item_id = create_resp.json()["id"]

    resp = client.patch(f"/api/v1/items/{item_id}", json={field: f"Widget{char}Pro"})

    assert resp.status_code == 422
    assert resp.json()["detail"][0]["loc"] == ["body", field]
    item = client.get(f"/api/v1/items/{item_id}").json()
    assert (item["name"], item["keywords"]) == ("Widget", "blue")


def test_item_text_accepts_printable_unicode(client):
    """Accented letters, symbols, CJK and spaces are not control characters."""
    name, keywords = "Café “Pro” — 50% off™", "größe, 日本"
    create_resp = client.post(
        "/api/v1/items/",
        json={"name": name, "keywords": keywords, "notify_email": "user@example.com"},
    )
    assert create_resp.status_code == 201
    assert (create_resp.json()["name"], create_resp.json()["keywords"]) == (name, keywords)

    item_id = create_resp.json()["id"]
    resp = client.patch(f"/api/v1/items/{item_id}", json={"name": "Café Neo", "keywords": None})
    assert resp.status_code == 200
    assert (resp.json()["name"], resp.json()["keywords"]) == ("Café Neo", None)
