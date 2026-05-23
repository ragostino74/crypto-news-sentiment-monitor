"""Decrypt.me news source adapter.

Decrypt offers strong altcoin and NFT coverage with a modern tech angle.
Their RSS feed includes full article content in most entries.
"""

from __future__ import annotations

import logging
from typing import Any

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
    key="decrypt",
    display_name="Decrypt",
    rss_url="https://decrypt.co/feed",
    fallback_urls=["https://rss.app/explore/decrypt-media"],
)


class DecryptAdapter(SourceAdapter):
    """Fetches articles from the Decrypt RSS feed."""

    metadata = _METADATA

    def fetch_latest(self, limit: int = 20) -> list[RawArticle]:
        """Fetch latest articles from Decrypt RSS."""
        retry_cfg = RetryConfig(max_attempts=3, base_delay=2.0, jitter=0.5)

        # Try primary URL, then fall back
        feed = None
        for url in [_METADATA.rss_url] + _METADATA.fallback_urls:
            try:
                feed = retry_with_backoff(fetch_rss_feed, config=retry_cfg, feed_url=url)
                if feed and "entries" in feed:
                    break
            except Exception as exc:
                logger.warning("Decrypt RSS primary failed (%s), trying fallback...", exc)
                continue

        if not feed or "entries" not in feed:
            logger.error("All Decrypt fetch attempts exhausted")
            return []

        articles: list[RawArticle] = []
        for entry in feed.get("entries", [])[:limit]:
            article = self._parse_entry(_METADATA.display_name, entry)
            if article is not None:
                article = enrich_article_content(article)
                articles.append(article)

        logger.info(
            "Decrypt fetched %d raw entries (%d valid articles)",
            len(feed.get("entries", [])),
            len(articles),
        )
        return articles

    def _parse_entry(self, source_name: str, entry: dict[str, Any]) -> RawArticle | None:
        """Parse a single Decrypt feedparser entry."""
        return entry_to_article(source_name, entry)


def fetch_latest(limit: int = 20) -> list[RawArticle]:
    """Convenience function for backward compatibility."""
    return DecryptAdapter().fetch_latest(limit=limit)
