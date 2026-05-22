"""SQLAlchemy model for persisted crypto news articles.

Maps the cleaned + sentiment-enriched article pipeline output into a
relational table with full deduplication support via ``article_hash``.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, relationship


# ------------------------------------------------------------------ Base ---


class Base(DeclarativeBase):
    """Shared declarative base for all models."""

    pass


# ------------------------------------------------------------------ Helper ---


def compute_article_hash(source: str, url: str) -> str:
    """Deterministic SHA-256 hash of ``source + url``.

    This is the primary deduplication key — two articles from different
    sources pointing to the same canonical URL get the same hash.
    """
    raw = f"{source.strip().lower()}|{url.strip().lower()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def now_utc() -> datetime:
    """Return current UTC timestamp."""
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ Model ---


class Article(Base):
    """Persisted article with sentiment scores.

    Each row is uniquely identified by ``article_hash`` (derived from
    source + URL).  The ``run_id`` foreign key links the article to its
    collection run for aggregation and reporting.
    """

    __tablename__ = "articles"

    # Primary key — surrogate autoincrement.
    id: int = Column(Integer, primary_key=True, autoincrement=True)

    # Deduplication key (source + url hash).
    article_hash: str = Column(String(64), unique=True, nullable=False, index=True)

    # Article metadata.
    source: str = Column(String(100), nullable=False, index=True)
    title: str = Column(Text, nullable=False)
    url: str = Column(String(2048), nullable=False, index=True)
    published_at: Optional[datetime] = Column(DateTime(timezone=True), nullable=True, index=True)
    summary: str = Column(Text, nullable=True, default="")
    content: str = Column(Text, nullable=True, default="")

    # When the scraper fetched this article.
    scraped_at: datetime = Column(
        DateTime(timezone=True), nullable=False, default=now_utc
    )

    # Sentiment scores (set by the sentiment module).
    sentiment_pos: float = Column(Float, nullable=True, default=None)
    sentiment_neu: float = Column(Float, nullable=True, default=None)
    sentiment_neg: float = Column(Float, nullable=True, default=None)
    sentiment_compound: float = Column(Float, nullable=True, default=None)
    sentiment_label: str = Column(String(20), nullable=True, default=None)
    sentiment_engine: str = Column(String(50), nullable=True, default="vader")

    # Link to the run that collected this article.
    run_id: Optional[int] = Column(Integer, ForeignKey("runs.id"), nullable=True)

    # ORM relationships.
    run = relationship("Run", back_populates="articles", lazy="select")

    __table_args__ = (
        UniqueConstraint("article_hash", name="uq_articles_hash"),
    )

    # ------------------------------------------------------------------- ORM ---

    def __repr__(self) -> str:
        return f"<Article id={self.id} title={self.title[:50]!r} source={self.source!r}>"
