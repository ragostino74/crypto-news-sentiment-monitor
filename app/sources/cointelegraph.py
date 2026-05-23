"""Cointelegraph news source adapter.

Cointelegraph's RSS feed provides summaries but NO full article content.
This adapter enriches each article by scraping the full text from the URL
when readability-lxml is available.
"""

from __future__ import annotations

import logging
from typing import Any

from dataclasses import replace as dc_replace

from .base import RawArticle, fetch_rss_feed, entry_to_article
from .base_adapter import (
    SourceAdapter,
    SourceMetadata,
    RetryConfig,
    enrich_article_content,
    retry_with_backoff,
)

logger = logging.getLogger(__name__)

# Cointelegraph RSS feed.
_METADATA = SourceMetadata(
    key="cointelegraph",
    display_name="Cointelegraph",
    rss_url="https://cointelegraph.com/rss",
)


class CointelegraphAdapter(SourceAdapter):
    """Fetches articles from the Cointelegraph RSS feed.

    Enriches content via web scraping since RSS only provides summaries.
    """

    metadata = _METADATA

    def fetch_latest(self, limit: int = 20) -> list[RawArticle]:
        """Fetch latest articles from Cointelegraph RSS."""
        retry_cfg = RetryConfig(max_attempts=3, base_delay=2.0, jitter=0.5)
        feed = retry_with_backoff(fetch_rss_feed, config=retry_cfg, feed_url=_METADATA.rss_url) or {}

        articles: list[RawArticle] = []
        for entry in feed.get("entries", [])[:limit]:
            article = self._parse_entry(_METADATA.display_name, entry)
            if article is not None:
                # Enrich content — Cointelegraph RSS has NO full text
                article = enrich_article_content(article)
                articles.append(article)

        logger.info(
            "Cointelegraph fetched %d raw entries (%d valid articles)",
            len(feed.get("entries", [])),
            len(articles),
        )
        return articles

    def _parse_entry(self, source_name: str, entry: dict[str, Any]) -> RawArticle | None:
        """Parse a single Cointelegraph feedparser entry."""
        return entry_to_article(source_name, entry)


def fetch_latest(limit: int = 20) -> list[RawArticle]:
    """Convenience function for backward compatibility."""
    return CointelegraphAdapter().fetch_latest(limit=limit)
