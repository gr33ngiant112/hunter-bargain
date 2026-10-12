"""The record of each price check of all items (check_runs)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from hunter_bargain.db import get_db
from hunter_bargain.models import CheckRun
from hunter_bargain.schemas import CheckRunResponse

router = APIRouter(tags=["runs"])
DbSession = Annotated[Session, Depends(get_db)]


@router.get("/runs", response_model=list[CheckRunResponse])
def list_runs(db: DbSession, limit: Annotated[int, Query(ge=1, le=100)] = 10) -> list[CheckRun]:
    """The latest checks of all items, by the daily job or POST /prices/check-all, newest first.

    Each says when it started and finished and whether it succeeded, and once it has ended, how
    many items it checked, searches it used, engine errors it met and alerts it sent.
    """
    return list(db.scalars(select(CheckRun).order_by(CheckRun.id.desc()).limit(limit)))
