"""Tests for app.core.db — persistence layer."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import inspect as sa_inspect, text

from app.core import db as db_mod


# ------------------------------------------------------------------ Fixtures ---


@pytest.fixture()
def tmp_db(tmp_path):
    """Create an in-memory test database and return the connection."""
    url = "sqlite:///:memory:"
    # Ensure engine/session caches are cleared for a clean slate.
    original_engine = db_mod._engine
    original_factory = db_mod._session_factory
    try:
        db_mod._engine = None
        db_mod._session_factory = None

        db_mod.init_db(url)

        conn = sqlite3.connect(":memory:")
        yield url, conn
    finally:
        db_mod._engine = original_engine
        db_mod._session_factory = original_factory
        conn.close()


@pytest.fixture()
def session(tmp_db):
    """Return a SQLAlchemy session bound to the in-memory test DB."""
    url, _ = tmp_db
    engine = db_mod.get_engine(url)
    Session = db_mod.get_session_factory(engine)
    s = Session()
    yield s
    s.close()


# ------------------------------------------------------------------ Helpers ---


def _sample_article_kwargs():
    return {
        "source": "test_source",
        "title": "Bitcoin hits new high",
        "url": "https://example.com/btc-high",
        "published_at": datetime(2025, 3, 15, 10, 0, tzinfo=timezone.utc),
        "summary": "Bitcoin surged past $90k today.",
        "content": "Full article text here.",
        "sentiment_pos": 0.45,
        "sentiment_neu": 0.30,
        "sentiment_neg": 0.25,
        "sentiment_compound": 0.7269,
        "sentiment_label": "positive",
        "sentiment_engine": "vader",
    }


def _sample_run_kwargs():
    return {
        "started_at": datetime(2025, 3, 15, 9, 0, tzinfo=timezone.utc),
        "finished_at": datetime(2025, 3, 15, 9, 5, tzinfo=timezone.utc),
        "articles_fetched": 100,
        "articles_new": 42,
        "global_sentiment_score": 0.32,
        "global_sentiment_label": "positive",
        "status": "completed",
    }


# ================================================================== Tests: Models ---


class TestArticleModel:
    """Tests for the Article SQLAlchemy model."""

    def test_article_schema(self, session):
        """Ensure all expected columns exist on the articles table."""
        from app.models.article import Article  # noqa: PLC0415

        mapper = sa_inspect(Article)
        col_names = {c.key for c in mapper.column_attrs}

        expected = {
            "id",
            "article_hash",
            "source",
            "title",
            "url",
            "published_at",
            "summary",
            "content",
            "sentiment_pos",
            "sentiment_neu",
            "sentiment_neg",
            "sentiment_compound",
            "sentiment_label",
            "sentiment_engine",
            "scraped_at",
            "run_id",
        }
        assert expected.issubset(col_names), (
            f"Missing columns: {expected - col_names}"
        )

    def test_compute_hash_is_deterministic(self):
        """Same (source, url) always yields the same hash."""
        from app.models.article import compute_article_hash  # noqa: PLC0415

        h1 = compute_article_hash("coinbase", "https://example.com/a")
        h2 = compute_article_hash("coinbase", "https://example.com/a")
        assert h1 == h2

    def test_compute_hash_differs_per_source(self):
        """Different source → different hash for the same URL."""
        from app.models.article import compute_article_hash  # noqa: PLC0415

        h1 = compute_article_hash("source_a", "https://example.com/x")
        h2 = compute_article_hash("source_b", "https://example.com/x")
        assert h1 != h2


class TestRunModel:
    """Tests for the Run SQLAlchemy model."""

    def test_run_schema(self, session):
        """Ensure all expected columns exist on the runs table."""
        from app.models.run import Run  # noqa: PLC0415

        mapper = sa_inspect(Run)
        col_names = {c.key for c in mapper.column_attrs}

        expected = {
            "id",
            "started_at",
            "finished_at",
            "articles_fetched",
            "articles_new",
            "global_sentiment_score",
            "global_sentiment_label",
            "status",
            "error_message",
        }
        assert expected.issubset(col_names), (
            f"Missing columns: {expected - col_names}"
        )


# ================================================================== Tests: CRUD ---


class TestUpsertArticle:
    """Tests for upsert_article deduplication and insert logic."""

    def test_insert_new_article(self, session):
        """First call with unique (source, url) inserts a row."""
        kwargs = _sample_article_kwargs()
        result = db_mod.upsert_article(session, **kwargs)
        assert result is True  # new row inserted
        from app.models.article import Article  # noqa: PLC0415

        count = session.query(Article).count()
        assert count == 1

    def test_upsert_updates_existing(self, session):
        """Second call with same (source, url) updates instead of inserting."""
        kwargs = _sample_article_kwargs()
        first = db_mod.upsert_article(session, **kwargs)
        assert first is True

        # Same source + url, different title.
        kwargs["title"] = "Bitcoin updated"
        second = db_mod.upsert_article(session, **kwargs)
        assert second is False  # no new row

        from app.models.article import Article  # noqa: PLC0415

        article = session.query(Article).first()
        assert article.title == "Bitcoin updated"
        count = session.query(Article).count()
        assert count == 1  # still one row


class TestArticleQueries:
    """Tests for get_latest_articles, get_article_by_hash."""

    def test_get_latest_returns_ordered(self, session):
        """Articles are returned ordered by scraped_at DESC."""
        from datetime import timezone as tz  # noqa: PLC0415

        articles = []
        for i in range(3):
            kwargs = _sample_article_kwargs()
            kwargs["title"] = f"Article {i}"
            kwargs["url"] = f"https://example.com/a{i}"
            db_mod.upsert_article(session, **kwargs)
            # Inject different scraped_at via raw SQL to avoid monotonic time.
            session.execute(
                text(
                    f"""
                    UPDATE articles SET scraped_at = datetime('now', '-{10-i} seconds')
                    WHERE url = '{kwargs["url"]}'
                    """
                )
            )
            articles.append(kwargs["title"])

        results = db_mod.get_latest_articles(session, limit=5)
        titles = [a.title for a in results]
        assert titles == ["Article 2", "Article 1", "Article 0"]

    def test_get_article_by_hash(self, session):
        """Find article by its computed hash."""
        from app.models.article import Article, compute_article_hash  # noqa: PLC0415

        kwargs = _sample_article_kwargs()
        db_mod.upsert_article(session, **kwargs)

        h = compute_article_hash(kwargs["source"], kwargs["url"])
        found = db_mod.get_article_by_hash(session, h)
        assert found is not None
        assert found.url == kwargs["url"]


class TestRunCRUD:
    """Tests for create_run, finish_run, get_last_run."""

    def test_create_and_finish(self, session):
        """Full run lifecycle: create → finish with stats."""
        run = db_mod.create_run(session, status="running", articles_fetched=50)
        assert run is not None
        assert run.status == "running"
        assert run.id is not None

        finished = db_mod.finish_run(
            session,
            run.id,
            articles_new=20,
            global_sentiment_score=0.45,
            global_sentiment_label="positive",
            status="completed",
        )
        assert finished is not None
        assert finished.status == "completed"
        assert finished.articles_new == 20

    def test_finish_nonexistent_returns_none(self, session):
        result = db_mod.finish_run(session, 99999)
        assert result is None

    def test_get_last_run(self, session):
        """get_last_run returns the most recently started run."""
        from app.models.run import Run  # noqa: PLC0415

        r1 = db_mod.create_run(session, articles_fetched=10)
        r2 = db_mod.create_run(session, articles_fetched=20)

        last = db_mod.get_last_run(session)
        assert last.id == r2.id

    def test_get_run_by_id(self, session):
        run = db_mod.create_run(session)
        fetched = db_mod.get_run_by_id(session, run.id)
        assert fetched is not None
        assert fetched.id == run.id


class TestDropAllTables:
    """Tests for the drop_all_tables helper (test-only)."""

    def test_drop_recreates(self):
        """After drop, init_db can recreate the tables."""
        url = "sqlite:///:memory:"
        db_mod._engine = None
        try:
            db_mod.init_db(url)
            engine = db_mod.get_engine(url)
            info = sa_inspect(engine)
            assert "articles" in info.get_table_names()

            db_mod.drop_all_tables(url)
            info2 = sa_inspect(engine)
            assert "articles" not in info2.get_table_names()
        finally:
            db_mod._engine = None
