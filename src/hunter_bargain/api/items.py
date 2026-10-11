"""CRUD endpoints for tracked items."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from hunter_bargain.db import get_db
from hunter_bargain.models import Item
from hunter_bargain.schemas import ItemCreate, ItemResponse, ItemUpdate

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
