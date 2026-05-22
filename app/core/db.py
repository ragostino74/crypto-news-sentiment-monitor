"""Database infrastructure for crypto-news-sentiment-monitor.

Covers:
  - Engine creation (file-based SQLite by default, in-memory for tests)
  - Declarative base import (pulls in all models via side-effect)
  - Session factory
  - Table initialisation
  - Article CRUD with deduplication
  - Run lifecycle management
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional, TypeVar

from sqlalchemy import (
    create_engine,
    inspect,
    select,
)
from sqlalchemy.orm import (
    Session,
    sessionmaker,
)

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ Types ---

T = TypeVar("T")


# ------------------------------------------------------------------ Engine --


_DEFAULT_DB_URL = "sqlite:///data/crypto_news.db"
_engine: object | None = None


def _build_engine(db_url: str | None = None):
    """Create and cache a SQLAlchemy engine.

    Args:
        db_url: SQLite connection string.  Defaults to
            ``sqlite:///data/crypto_news.db``.  Use an in-memory URL for tests:
            ``sqlite:///:memory:``.

    Returns:
        A configured ``sqlalchemy.engine.Engine``.
    """
    global _engine
    if _engine is not None:
        return _engine  # type: ignore[return-value]

    url = db_url or _DEFAULT_DB_URL
    kwargs: dict[str, object] = {
        "echo": False,
    }
    # SQLite-specific pragmas for concurrency safety.
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        kwargs["pool_pre_ping"] = True

    _engine = create_engine(url, **kwargs)  # type: ignore[arg-type]
    return _engine


def get_engine(db_url: str | None = None):
    """Return the cached engine, creating it if necessary."""
    return _build_engine(db_url)  # type: ignore[return-value]


# ------------------------------------------------------------------ Session --


_session_factory: object | None = None


def get_session_factory(bind: object | None = None):
    """Return a ``sessionmaker`` bound to the current engine.

    Args:
        bind: Explicit engine / connection.  Falls back to
            :func:`get_engine`.
    """
    global _session_factory
    if _session_factory is not None:
        return _session_factory  # type: ignore[return-value]

    engine = bind or get_engine()
    _session_factory = sessionmaker(bind=engine)  # type: ignore[arg-type]
    return _session_factory


def get_session():
    """Create and return a new ``Session``."""
    return get_session_factory()()


# ------------------------------------------------------------------ Context manager --


@contextmanager
def session_scope():
    """Provide a transactional scope around a series of operations.

    Usage::

        with session_scope() as session:
            session.add(article)
    """
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ------------------------------------------------------------------ Initialisation ---


def init_db(db_url: str | None = None):
    """Create all tables defined in the ORM models.

    Imports all model modules as a side-effect to ensure ``Base.metadata``
    is fully populated before calling ``create_all``.

    Args:
        db_url: Optional SQLite URL override.  Defaults to the module-level
            default.
    """
    # Import all models so their ``__tablename__`` classes register with Base.
    # isort: off
    import app.models.article  # noqa: F401
    import app.models.run        # noqa: F401
    # isort: on

    from app.models.article import Base  # noqa: PLC0415

    engine = get_engine(db_url)
    Base.metadata.create_all(engine)
    logger.info("Database initialised (url=%s)", db_url or _DEFAULT_DB_URL)


# ------------------------------------------------------------------ Article CRUD ---


def upsert_article(
    session: Session,
    *,
    source: str,
    title: str,
    url: str,
    published_at: datetime | None = None,
    summary: str = "",
    content: str = "",
    sentiment_pos: float | None = None,
    sentiment_neu: float | None = None,
    sentiment_neg: float | None = None,
    sentiment_compound: float | None = None,
    sentiment_label: str | None = None,
    sentiment_engine: str = "vader",
    run_id: int | None = None,
) -> bool:
    """Insert an article or update it if the hash already exists.

    Deduplication key is ``article_hash`` (derived from source + URL).

    Args:
        session: Active SQLAlchemy session.
        **kwargs: Article fields matching the ``Article`` model columns.

    Returns:
        ``True`` if a new row was inserted, ``False`` if an existing row
        was updated in place.
    """
    from app.models.article import Article, compute_article_hash  # noqa: PLC0415

    article_hash = compute_article_hash(source, url)

    existing = session.execute(
        select(Article).where(Article.article_hash == article_hash)
    ).scalar_one_or_none()

    if existing is not None:
        # Update only — no new row created.
        existing.title = title
        existing.url = url
        existing.published_at = published_at
        existing.summary = summary
        existing.content = content
        existing.scraped_at = datetime.now(timezone.utc)
        if sentiment_pos is not None:
            existing.sentiment_pos = sentiment_pos
        if sentiment_neu is not None:
            existing.sentiment_neu = sentiment_neu
        if sentiment_neg is not None:
            existing.sentiment_neg = sentiment_neg
        if sentiment_compound is not None:
            existing.sentiment_compound = sentiment_compound
        if sentiment_label is not None:
            existing.sentiment_label = sentiment_label
        if sentiment_engine is not None:
            existing.sentiment_engine = sentiment_engine
        if run_id is not None:
            existing.run_id = run_id
        return False

    # New article — create and insert.
    new_article = Article(
        article_hash=article_hash,
        source=source,
        title=title,
        url=url,
        published_at=published_at,
        summary=summary,
        content=content,
        scraped_at=datetime.now(timezone.utc),
        sentiment_pos=sentiment_pos,
        sentiment_neu=sentiment_neu,
        sentiment_neg=sentiment_neg,
        sentiment_compound=sentiment_compound,
        sentiment_label=sentiment_label,
        sentiment_engine=sentiment_engine,
        run_id=run_id,
    )
    session.add(new_article)
    return True


def get_latest_articles(
    session: Session,
    limit: int = 50,
    source: str | None = None,
) -> list:
    """Return the N most recently scraped articles.

    Args:
        session: Active SQLAlchemy session.
        limit: Maximum number of rows to return (default 50).
        source: Optional filter by source name.

    Returns:
        List of ``Article`` ORM objects ordered by ``scraped_at DESC``.
    """
    from app.models.article import Article  # noqa: PLC0415

    stmt = select(Article).order_by(Article.scraped_at.desc()).limit(limit)

    if source:
        stmt = stmt.where(Article.source == source)

    return list(session.execute(stmt).scalars().all())


def get_article_by_hash(
    session: Session,
    article_hash: str,
) -> object | None:
    """Fetch a single article by its deduplication hash.

    Returns ``None`` if no match is found.
    """
    from app.models.article import Article  # noqa: PLC0415

    return session.execute(
        select(Article).where(Article.article_hash == article_hash)
    ).scalar_one_or_none()


# ------------------------------------------------------------------ Run CRUD ---


def create_run(
    session: Session,
    *,
    status: str = "running",
    articles_fetched: int = 0,
) -> object:
    """Create a new ``Run`` record with ``status=running``.

    Args:
        session: Active SQLAlchemy session.
        status: Initial status (default "running").
        articles_fetched: Count of raw articles fetched before dedup.

    Returns:
        The newly created ``Run`` ORM instance.
    """
    from app.models.run import Run  # noqa: PLC0415

    run = Run(
        started_at=datetime.now(timezone.utc),
        status=status,
        articles_fetched=articles_fetched,
    )
    session.add(run)
    session.flush()  # populate the id
    return run


def finish_run(
    session: Session,
    run_id: int,
    *,
    articles_new: int = 0,
    global_sentiment_score: float | None = None,
    global_sentiment_label: str | None = None,
    status: str = "completed",
    error_message: str | None = None,
) -> object | None:
    """Finalise a ``Run`` with end time, counts, and aggregate sentiment.

    Args:
        session: Active SQLAlchemy session.
        run_id: ID of the run to finalise.
        articles_new: Number of articles persisted after dedup.
        global_sentiment_score: Mean compound score across all articles.
        global_sentiment_label: Dominant sentiment label.
        status: Final status (default "completed").
        error_message: Optional error description (for failure runs).

    Returns:
        The updated ``Run`` ORM instance, or ``None`` if not found.
    """
    from app.models.run import Run  # noqa: PLC0415

    run = session.execute(
        select(Run).where(Run.id == run_id)
    ).scalar_one_or_none()

    if run is None:
        logger.warning("finish_run: no run found with id=%d", run_id)
        return None

    run.finished_at = datetime.now(timezone.utc)
    run.articles_new = articles_new
    run.global_sentiment_score = global_sentiment_score
    run.global_sentiment_label = global_sentiment_label
    run.status = status
    run.error_message = error_message

    return run


def get_last_run(session: Session) -> object | None:
    """Return the most recent ``Run`` record.

    Returns ``None`` if no runs exist.
    """
    from app.models.run import Run  # noqa: PLC0415

    return session.execute(
        select(Run).order_by(Run.started_at.desc()).limit(1)
    ).scalar_one_or_none()


def get_run_by_id(session: Session, run_id: int) -> object | None:
    """Fetch a single ``Run`` by ID.

    Returns ``None`` if not found.
    """
    from app.models.run import Run  # noqa: PLC0415

    return session.execute(
        select(Run).where(Run.id == run_id)
    ).scalar_one_or_none()


# ------------------------------------------------------------------ Drop tables (tests only) ---


def drop_all_tables(db_url: str | None = None):
    """Drop all tables.  Intended for test cleanup, NOT production."""
    # Import models first.
    import app.models.article  # noqa: F401
    import app.models.run        # noqa: F401

    from app.models.article import Base  # noqa: PLC0415

    engine = get_engine(db_url)
    Base.metadata.drop_all(engine)
    logger.info("All tables dropped (url=%s)", db_url or _DEFAULT_DB_URL)
