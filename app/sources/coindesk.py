"""CoinDesk news source adapter."""

from __future__ import annotations

import logging
from typing import Any

from .base import RawArticle, fetch_rss_feed, entry_to_article

logger = logging.getLogger(__name__)

# CoinDesk publishes via FeedBurner (their WordPress feed).
_RSS_URL = "https://feeds.feedburner.com/coindesk"


def fetch_latest(limit: int = 20) -> list[RawArticle]:
    """Fetch latest articles from CoinDesk RSS.

    Args:
        limit: Maximum number of articles to return.

    Returns:
        List of RawArticle objects. Never raises — returns [] on error.
    """
    try:
        feed = fetch_rss_feed(_RSS_URL)
    except Exception as exc:
        logger.error("CoinDesk RSS fetch failed: %s", exc)
        return []

    articles: list[RawArticle] = []
    for entry in feed.entries[:limit]:
        article = _parse_entry(entry)
        if article is not None:
            articles.append(article)

    logger.info("CoinDesk fetched %d articles (%d parsed)", len(feed.entries), len(articles))
    return articles


def _parse_entry(entry: dict[str, Any]) -> RawArticle | None:
    """Parse a single feedparser entry into a RawArticle."""
    article = entry_to_article("CoinDesk", entry)
    if article is not None:
        # CoinDesk RSS may omit content entirely.  If summary is also empty,
        # try to grab text from tags or leave it blank (not an error).
        if not article.summary and not article.content:
            tag_names = [t.get("term", "") for t in entry.get("tags", []) if isinstance(t, dict)]
            tags_str = " | ".join(tag_names)
            if tags_str:
                article.summary = f"[{tags_str}] No summary available in RSS feed."
    return article
