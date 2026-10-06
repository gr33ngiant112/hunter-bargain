"""On-demand price check endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from hunter_bargain.db import get_db
from hunter_bargain.models import Item
from hunter_bargain.schemas import CheckAllStarted, PriceCheckResult
from hunter_bargain.services.scheduler import start_check_all
from hunter_bargain.services.searcher import run_price_check

router = APIRouter(prefix="/prices", tags=["prices"])
DbSession = Annotated[Session, Depends(get_db)]


@router.post("/check/{item_id}", response_model=PriceCheckResult)
def check_price(item_id: int, db: DbSession) -> PriceCheckResult:
    """Trigger an on-demand price check for a specific item."""
    item = db.query(Item).filter(Item.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail=f"Item {item_id} not found")
    return run_price_check(item=item, db=db)


@router.post(
    "/check-all",
    status_code=202,
    response_model=CheckAllStarted,
    responses={409: {"description": "A price check of all items is already running"}},
)
def check_all_prices() -> CheckAllStarted:
    """Start a price check of ALL tracked items in the background.

    Answers 202 at once. As with the daily job, the results go to the price history, the log and
    alert emails. Answers 409 while a check of all items, this endpoint's or the daily job's, runs.
    """
    if not start_check_all():
        raise HTTPException(status_code=409, detail="A price check of all items is already running")
    return CheckAllStarted()
