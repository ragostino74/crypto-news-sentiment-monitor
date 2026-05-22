"""SQLAlchemy model for monitoring runs.

Each run represents one complete execution of the collection →
normalise → sentiment pipeline and captures summary statistics.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Column, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import relationship

from app.models.article import Base


# ------------------------------------------------------------------ Model ---


class Run(Base):
    """A single pipeline run: fetch → normalise → sentiment.

    Stores the lifecycle timestamps, counts, and aggregate sentiment
    so that downstream dashboards / alerts can be built on top of this
    table.
    """

    __tablename__ = "runs"

    # Primary key — surrogate autoincrement.
    id: int = Column(Integer, primary_key=True, autoincrement=True)

    # Lifecycle timestamps.
    started_at: datetime = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    finished_at: Optional[datetime] = Column(
        DateTime(timezone=True), nullable=True
    )

    # Counts.
    articles_fetched: int = Column(Integer, nullable=False, default=0)     # raw count before dedup
    articles_new: int = Column(Integer, nullable=False, default=0)           # persisted after dedup

    # Aggregate sentiment (set by the aggregation step).
    global_sentiment_score: Optional[float] = Column(Float, nullable=True, default=None)
    global_sentiment_label: Optional[str] = Column(String(20), nullable=True, default=None)

    # Status and optional error.
    status: str = Column(String(30), nullable=False, default="pending")     # pending / running / completed / failed
    error_message: Optional[str] = Column(Text, nullable=True, default=None)

    # ------------------------------------------------------------------- ORM ---

    articles = relationship("Article", back_populates="run", lazy="select")

    def __repr__(self) -> str:
        return (
            f"<Run id={self.id} status={self.status!r} "
            f"fetched={self.articles_fetched} new={self.articles_new}>"
        )
