"""SQLAlchemy ORM models for tracked items and price records."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from hunter_bargain.db import Base


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class Item(Base):
    """An item the user wants to track prices for."""

    __tablename__ = "items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # Optional comma-separated terms (e.g. "64GB, midnight" for an iPhone): added to the search
    # query, and every word of every term must be in a listing's title (services/searcher.py)
    keywords: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Target price — notify when a result is at or below this threshold
    target_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Email address to notify for this item
    notify_email: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    # Relationship to price records
    price_records: Mapped[list[PriceRecord]] = relationship(
        back_populates="item",
        cascade="all, delete-orphan",
        order_by="PriceRecord.checked_at.desc()",
    )

    def __repr__(self) -> str:
        return f"<Item id={self.id} name={self.name!r}>"


class PriceRecord(Base):
    """A single price observation for a tracked item."""

    __tablename__ = "price_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("items.id"), nullable=False, index=True
    )
    price: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    source: Mapped[str] = mapped_column(String(255), nullable=False)  # e.g. "google_shopping"
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    checked_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    item: Mapped[Item] = relationship(back_populates="price_records")

    def __repr__(self) -> str:
        return f"<PriceRecord item_id={self.item_id} price={self.price} source={self.source!r}>"
