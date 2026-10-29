"""SQLAlchemy 2 models for served hotel results.

The full :class:`~hotel_curator.schemas.HotelDetail` of each hotel is stored
as JSON (it is produced in one batch and only ever read whole). Fields used
to filter and sort search results are copied into indexed columns, and one
row per (hotel, attribute) supports "strong in X" / "not weak in X"
filters.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base for all tables."""


class Hotel(Base):
    """One hotel and its tier result."""

    __tablename__ = "hotels"

    hotel_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    name_lower: Mapped[str] = mapped_column(String(200), index=True)
    city: Mapped[str] = mapped_column(String(80), index=True)
    country: Mapped[str] = mapped_column(String(80))
    tier: Mapped[str] = mapped_column(String(32), index=True)
    tier_rank: Mapped[int] = mapped_column(Integer, index=True)
    sort_score: Mapped[float] = mapped_column(Float)
    reviews_eligible: Mapped[int] = mapped_column(Integer)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON)

    attributes: Mapped[list["AttributeScore"]] = relationship(
        back_populates="hotel", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_hotels_rank_score", "tier_rank", "sort_score"),)


class AttributeScore(Base):
    """One attribute grade of one hotel (for filtering)."""

    __tablename__ = "attribute_scores"

    hotel_id: Mapped[str] = mapped_column(
        ForeignKey("hotels.hotel_id", ondelete="CASCADE"), primary_key=True
    )
    attribute: Mapped[str] = mapped_column(String(32), primary_key=True)
    grade: Mapped[str] = mapped_column(String(16))
    observed: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)

    hotel: Mapped[Hotel] = relationship(back_populates="attributes")

    __table_args__ = (Index("ix_attribute_grade", "attribute", "grade"),)


class DatasetInfo(Base):
    """What is loaded: one row per seed, newest last."""

    __tablename__ = "dataset_info"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    results_sha256: Mapped[str] = mapped_column(String(64))
    params_version: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(120))
    hotels: Mapped[int] = mapped_column(Integer)
    loaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # First and last month ("2015-08") of the usable reviews; empty in rows
    # seeded before migration 0002 until the next seed fills them.
    first_month: Mapped[str | None] = mapped_column(String(7), nullable=True)
    last_month: Mapped[str | None] = mapped_column(String(7), nullable=True)
