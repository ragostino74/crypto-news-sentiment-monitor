"""The Block news source adapter."""

from __future__ import annotations

import logging

from .base import RawArticle, fetch_rss_feed, entry_to_article

logger = logging.getLogger(__name__)

# The Block publishes via RSS at /rss.xml.
_RSS_URL = "https://www.theblock.co/rss.xml"


def fetch_latest(limit: int = 20) -> list[RawArticle]:
    """Fetch latest articles from The Block RSS.

    Args:
        limit: Maximum number of articles to return.

    Returns:
        List of RawArticle objects. Never raises — returns [] on error.
    """
    try:
        feed = fetch_rss_feed(_RSS_URL)
    except Exception as exc:
        logger.error("The Block RSS fetch failed: %s", exc)
        return []

    articles: list[RawArticle] = []
    for entry in feed.entries[:limit]:
        article = _parse_entry(entry)
        if article is not None:
            articles.append(article)

    logger.info(
        "The Block fetched %d articles (%d parsed)", len(feed.entries), len(articles)
    )
    return articles


def _parse_entry(entry: dict) -> RawArticle | None:
    """Parse a single The Block feedparser entry."""
    return entry_to_article("The Block", entry)
