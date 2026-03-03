"""Tests for item CRUD API endpoints."""


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
