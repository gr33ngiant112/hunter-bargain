"""Tests for the scheduled price check job."""

import logging
from unittest.mock import patch

from hunter_bargain.models import Item
from hunter_bargain.schemas import PriceCheckResult
from hunter_bargain.services.scheduler import _scheduled_price_check


def test_scheduled_check_logs_item_names_with_repr(db_session, caplog):
    """Line breaks in item names stay inside their log records (logged with %r)."""
    checked = Item(name="Widget\nFake record", notify_email="a@x.com")
    failing = Item(name="Gadget\r\nFake record", notify_email="b@x.com")
    db_session.add_all([checked, failing])
    db_session.commit()
    checked_id, failing_id = checked.id, failing.id

    def fake_check(item: Item, db: object) -> PriceCheckResult:
        if item.id == failing_id:
            raise RuntimeError("engine unavailable")
        return PriceCheckResult(
            item_id=item.id,
            item_name=item.name,
            lowest_price=None,
            lowest_source=None,
            lowest_url=None,
            results_count=0,
            records=[],
        )

    # The job's own session is the test database; run_price_check is faked, so no engine runs.
    with (
        patch("hunter_bargain.services.scheduler.SessionLocal", return_value=db_session),
        patch("hunter_bargain.services.scheduler.run_price_check", side_effect=fake_check),
        caplog.at_level(logging.INFO, logger="hunter_bargain.services.scheduler"),
    ):
        _scheduled_price_check()

    messages = [record.getMessage() for record in caplog.records]
    assert f"Item {checked_id} ('Widget\\nFake record'): 0 results, lowest=$N/A" in messages
    assert f"Price check failed for item {failing_id} ('Gadget\\r\\nFake record')" in messages
