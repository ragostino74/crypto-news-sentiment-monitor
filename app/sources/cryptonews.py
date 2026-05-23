"""CryptoNews news source adapter.

CryptoNews (cryptonews.com) provides broad cryptocurrency coverage with daily
updates on markets, regulation, blockchain technology, and DeFi.
"""

from __future__ import annotations

import logging

from .base import RawArticle, fetch_rss_feed, entry_to_article
from .base_adapter import (
    SourceAdapter,
    SourceMetadata,
    RetryConfig,
    enrich_article_content,
    retry_with_backoff,
)

logger = logging.getLogger(__name__)

_METADATA = SourceMetadata(
    key="cryptonews",
    display_name="CryptoNews",
    rss_url="https://cryptonews.com/rss/",
)


class CryptoNewsAdapter(SourceAdapter):
    """Fetches articles from the CryptoNews RSS feed."""

    metadata = _METADATA

    def fetch_latest(self, limit: int = 20) -> list[RawArticle]:
        """Fetch latest articles from CryptoNews RSS."""
        retry_cfg = RetryConfig(max_attempts=3, base_delay=2.0, jitter=0.5)
        feed = retry_with_backoff(fetch_rss_feed, config=retry_cfg, feed_url=_METADATA.rss_url) or {}

        articles: list[RawArticle] = []
        for entry in feed.get("entries", [])[:limit]:
            article = self._parse_entry(_METADATA.display_name, entry)
            if article is not None:
                article = enrich_article_content(article)
                articles.append(article)

        logger.info(
            "CryptoNews fetched %d raw entries (%d valid articles)",
            len(feed.get("entries", [])),
            len(articles),
        )
        return articles

    def _parse_entry(self, source_name: str, entry: dict) -> RawArticle | None:
        """Parse a single CryptoNews feedparser entry."""
        return entry_to_article(source_name, entry)


def fetch_latest(limit: int = 20) -> list[RawArticle]:
    """Convenience function for backward compatibility."""
    return CryptoNewsAdapter().fetch_latest(limit=limit)
