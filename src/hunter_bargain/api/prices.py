"""On-demand price check endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from hunter_bargain.db import get_db
from hunter_bargain.models import Item
from hunter_bargain.schemas import PriceCheckResult
from hunter_bargain.services.searcher import run_price_check

router = APIRouter(prefix="/prices", tags=["prices"])


@router.post("/check/{item_id}", response_model=PriceCheckResult)
def check_price(item_id: int, db: Session = Depends(get_db)) -> PriceCheckResult:
    """Trigger an on-demand price check for a specific item."""
    item = db.query(Item).filter(Item.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail=f"Item {item_id} not found")
    return run_price_check(item=item, db=db)


@router.post("/check-all", response_model=list[PriceCheckResult])
def check_all_prices(db: Session = Depends(get_db)) -> list[PriceCheckResult]:
    """Trigger an on-demand price check for ALL tracked items."""
    items = db.query(Item).all()
    return [run_price_check(item=item, db=db) for item in items]
