"""CRUD endpoints for tracked items, and their price history."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from hunter_bargain.db import get_db
from hunter_bargain.models import Item, PriceRecord
from hunter_bargain.schemas import (
    ItemCreate,
    ItemResponse,
    ItemUpdate,
    PriceRecordResponse,
    PriceStats,
)
from hunter_bargain.services import history

router = APIRouter(prefix="/items", tags=["items"])
DbSession = Annotated[Session, Depends(get_db)]


@router.post("/", response_model=ItemResponse, status_code=status.HTTP_201_CREATED)
def create_item(payload: ItemCreate, db: DbSession) -> Item:
    """Add a new item to track."""
    item = Item(
        name=payload.name,
        keywords=payload.keywords,
        target_price=payload.target_price,
        notify_email=payload.notify_email,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.get("/", response_model=list[ItemResponse])
def list_items(db: DbSession) -> list[Item]:
    """List all tracked items."""
    return list(db.query(Item).order_by(Item.created_at.desc()).all())


@router.get("/{item_id}", response_model=ItemResponse)
def get_item(item_id: int, db: DbSession) -> Item:
    """Get a single tracked item by ID."""
    item = db.query(Item).filter(Item.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail=f"Item {item_id} not found")
    return item


@router.get("/{item_id}/prices", response_model=list[PriceRecordResponse])
def list_item_prices(
    item_id: int,
    db: DbSession,
    since: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> list[PriceRecord]:
    """The item's recorded prices, newest first: at most `limit`, and only from `since` on.

    A `since` without a time zone is read as UTC, the zone the prices are recorded in.
    """
    item = db.query(Item).filter(Item.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail=f"Item {item_id} not found")
    return history.recent_records(db, item.id, since=since, limit=limit)


@router.get("/{item_id}/stats", response_model=PriceStats)
def item_price_stats(
    item_id: int, db: DbSession, days: Annotated[int, Query(ge=1, le=3650)] = 90
) -> PriceStats:
    """The item's low and median price on each day of the last `days` days, its lowest price in
    that period, and its all-time low."""
    item = db.query(Item).filter(Item.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail=f"Item {item_id} not found")
    since = history.days_ago(days)
    return PriceStats(
        item_id=item.id,
        item_name=item.name,
        days=days,
        per_day=history.daily_prices(db, item.id, since),
        lowest_in_days=history.lowest_price(db, item.id, since),
        all_time_low=history.lowest_price(db, item.id),
    )


@router.patch("/{item_id}", response_model=ItemResponse)
def update_item(item_id: int, payload: ItemUpdate, db: DbSession) -> Item:
    """Update a tracked item. Only provided fields are changed."""
    item = db.query(Item).filter(Item.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail=f"Item {item_id} not found")

    update_data = payload.model_dump(exclude_unset=True)
    # A new target starts the alerts over: the next price that meets it is emailed (#9).
    if "target_price" in update_data and update_data["target_price"] != item.target_price:
        item.last_alert_price = None
        item.last_alerted_at = None
    for field, value in update_data.items():
        setattr(item, field, value)

    db.commit()
    db.refresh(item)
    return item


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(item_id: int, db: DbSession) -> None:
    """Remove an item from tracking. Deletes all associated price records."""
    item = db.query(Item).filter(Item.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail=f"Item {item_id} not found")
    db.delete(item)
    db.commit()
