"""Pydantic schemas for API request/response validation."""

from __future__ import annotations

import unicodedata
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, EmailStr, Field, field_validator

from hunter_bargain.config import settings


def _check_alert_recipient(address: str) -> str:
    """Reject an address outside ALERT_RECIPIENTS; the error does not reveal the allowed ones."""
    if not settings.is_alert_recipient(address):
        raise ValueError("address is not in ALERT_RECIPIENTS")
    return address


# A notify_email the API accepts: a valid address listed in ALERT_RECIPIENTS (otherwise 422).
AlertRecipient = Annotated[EmailStr, AfterValidator(_check_alert_recipient)]


def _reject_control_characters(value: str | None) -> str | None:
    """Reject text with control characters (Unicode category Cc: CR, LF, ESC, ...).

    Item names and keywords end up in alert email, log lines and CLI output, where these
    characters could split log records or hide terminal output. Shared by ItemCreate and
    ItemUpdate as a field validator, so it runs after the fields' own length checks and
    their error messages stay the same.
    """
    if value is not None and any(unicodedata.category(c) == "Cc" for c in value):
        raise ValueError("must not contain control characters")
    return value


# ---------- Item schemas ----------


class ItemCreate(BaseModel):
    """Schema for creating a new tracked item."""

    name: str = Field(..., min_length=1, max_length=255, description="Product name to search for")
    keywords: str | None = Field(None, description="Extra search keywords to refine results")
    target_price: float | None = Field(
        None, gt=0, description="Alert when price is at or below this"
    )
    notify_email: AlertRecipient = Field(
        ..., description="Email address for price alerts; must be in ALERT_RECIPIENTS"
    )

    _plain_text = field_validator("name", "keywords")(_reject_control_characters)


class ItemUpdate(BaseModel):
    """Schema for updating an existing tracked item (all fields optional)."""

    name: str | None = Field(None, min_length=1, max_length=255)
    keywords: str | None = None
    target_price: float | None = Field(None, gt=0)
    notify_email: AlertRecipient | None = None

    _plain_text = field_validator("name", "keywords")(_reject_control_characters)


class ItemResponse(BaseModel):
    """Schema for returning an item in API responses."""

    id: int
    name: str
    keywords: str | None
    target_price: float | None
    notify_email: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------- Price record schemas ----------


class PriceRecordResponse(BaseModel):
    """Schema for a single price observation."""

    id: int
    item_id: int
    price: float
    currency: str
    source: str
    url: str | None
    title: str | None
    checked_at: datetime

    model_config = {"from_attributes": True}


# ---------- Price check schemas ----------


class PriceCheckResult(BaseModel):
    """Result of an on-demand price check for one item."""

    item_id: int
    item_name: str
    lowest_price: float | None
    lowest_source: str | None
    # Seller of the lowest-priced listing (Google Shopping source, Bing seller); not stored.
    lowest_merchant: str | None = None
    lowest_url: str | None
    results_count: int
    records: list[PriceRecordResponse]
