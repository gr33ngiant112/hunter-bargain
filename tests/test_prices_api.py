"""Tests for the price check API endpoints."""

from unittest.mock import patch

from hunter_bargain.schemas import PriceCheckResult


def test_check_price_not_found(client):
    """POST /api/v1/prices/check/999 returns 404."""
    resp = client.post("/api/v1/prices/check/999")
    assert resp.status_code == 404


@patch("hunter_bargain.api.prices.run_price_check")
def test_check_price_success(mock_run, client):
    """POST /api/v1/prices/check/{id} triggers a price check."""
    # Create an item first
    create_resp = client.post(
        "/api/v1/items/",
        json={"name": "Gadget", "notify_email": "g@x.com", "target_price": 50.0},
    )
    item_id = create_resp.json()["id"]

    mock_run.return_value = PriceCheckResult(
        item_id=item_id,
        item_name="Gadget",
        lowest_price=39.99,
        lowest_source="google_shopping",
        lowest_merchant="Walmart",
        lowest_url="http://example.com",
        results_count=1,
        records=[],
    )

    resp = client.post(f"/api/v1/prices/check/{item_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["lowest_price"] == 39.99
    assert data["item_name"] == "Gadget"
    assert (data["lowest_source"], data["lowest_merchant"]) == ("google_shopping", "Walmart")
