"""Core infrastructure: database setup, engine, session factory, CRUD helpers."""

from app.core.db import (
    init_db,
    get_engine,
    get_session_factory,
    get_session,
    session_scope,
    upsert_article,
    finish_run,
    create_run,
    get_latest_articles,
    get_last_run,
    drop_all_tables,
)

__all__ = [
    "init_db",
    "get_engine",
    "get_session_factory",
    "get_session",
    "session_scope",
    "upsert_article",
    "finish_run",
    "create_run",
    "get_latest_articles",
    "get_last_run",
    "drop_all_tables",
]
