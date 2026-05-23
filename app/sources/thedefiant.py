"""The Defiant news source adapter.

The Defiant is a leading DeFi-focused news outlet with very high output (100+
articles per cycle). Covers decentralized finance, governance, protocols, and
DeFi markets.
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
    key="thedefiant",
    display_name="The Defiant",
    rss_url="https://thedefiant.io/feed/",
)


class TheDefiantAdapter(SourceAdapter):
    """Fetches articles from The Defiant RSS feed."""

    metadata = _METADATA

    def fetch_latest(self, limit: int = 20) -> list[RawArticle]:
        """Fetch latest articles from The Defiant RSS.

        The Defiant has very high output — request up to 100 raw entries and
        take the top `limit` valid ones.
        """
        retry_cfg = RetryConfig(max_attempts=3, base_delay=2.0, jitter=0.5)
        feed = retry_with_backoff(fetch_rss_feed, config=retry_cfg, feed_url=_METADATA.rss_url) or {}

        articles: list[RawArticle] = []
        for entry in feed.get("entries", [])[:limit]:
            article = self._parse_entry(_METADATA.display_name, entry)
            if article is not None:
                article = enrich_article_content(article)
                articles.append(article)

        logger.info(
            "The Defiant fetched %d raw entries (%d valid articles)",
            len(feed.get("entries", [])),
            len(articles),
        )
        return articles

    def _parse_entry(self, source_name: str, entry: dict) -> RawArticle | None:
        """Parse a single The Defiant feedparser entry."""
        return entry_to_article(source_name, entry)


def fetch_latest(limit: int = 20) -> list[RawArticle]:
    """Convenience function for backward compatibility."""
    return TheDefiantAdapter().fetch_latest(limit=limit)
