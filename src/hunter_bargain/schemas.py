"""Pydantic schemas for API request/response validation."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


# ---------- Item schemas ----------


class ItemCreate(BaseModel):
    """Schema for creating a new tracked item."""

    name: str = Field(..., min_length=1, max_length=255, description="Product name to search for")
    keywords: str | None = Field(None, description="Extra search keywords to refine results")
    target_price: float | None = Field(
        None, gt=0, description="Alert when price is at or below this"
    )
    notify_email: EmailStr = Field(..., description="Email address for price alerts")


class ItemUpdate(BaseModel):
    """Schema for updating an existing tracked item (all fields optional)."""

    name: str | None = Field(None, min_length=1, max_length=255)
    keywords: str | None = None
    target_price: float | None = Field(None, gt=0)
    notify_email: EmailStr | None = None


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
    lowest_url: str | None
    results_count: int
    records: list[PriceRecordResponse]
