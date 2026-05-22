"""Decrypt news source adapter."""

from __future__ import annotations

import logging
from typing import Any

from .base import RawArticle, fetch_rss_feed, entry_to_article

logger = logging.getLogger(__name__)

# Decrypt publishes via standard WordPress-style RSS.
_RSS_URL = "https://decrypt.co/feed"


def fetch_latest(limit: int = 20) -> list[RawArticle]:
    """Fetch latest articles from Decrypt RSS.

    Args:
        limit: Maximum number of articles to return.

    Returns:
        List of RawArticle objects. Never raises — returns [] on error.
    """
    try:
        feed = fetch_rss_feed(_RSS_URL)
    except Exception as exc:
        logger.error("Decrypt RSS fetch failed: %s", exc)
        return []

    articles: list[RawArticle] = []
    for entry in feed.entries[:limit]:
        article = _parse_entry(entry)
        if article is not None:
            articles.append(article)

    logger.info("Decrypt fetched %d articles (%d parsed)", len(feed.entries), len(articles))
    return articles


def _parse_entry(entry: dict[str, Any]) -> RawArticle | None:
    """Parse a single Decrypt feedparser entry."""
    article = entry_to_article("Decrypt", entry)
    if article is not None and not article.summary:
        # Decrypt sometimes has a plain-text summary in the entry body.
        # If it's empty, fall back to tags.
        tag_names = [t.get("term", "") for t in entry.get("tags", []) if isinstance(t, dict)]
        if tag_names:
            article.summary = f"[{', '.join(tag_names)}] Read more at source."
    return article
