"""CoinDesk news source adapter."""

from __future__ import annotations

import logging
from typing import Any

from dataclasses import replace as dc_replace

from .base import RawArticle, fetch_rss_feed, entry_to_article
from .base_adapter import SourceAdapter, SourceMetadata, RetryConfig, enrich_article_content, retry_with_backoff

logger = logging.getLogger(__name__)


# CoinDesk publishes via FeedBurner (their WordPress feed).
_METADATA = SourceMetadata(
    key="coindesk",
    display_name="CoinDesk",
    rss_url="https://feeds.feedburner.com/coindesk",
)


class CoinDeskAdapter(SourceAdapter):
    """Fetches articles from the CoinDesk RSS feed.

    Handles the case where FeedBurner returns entries with no content or summary,
    enriching via article scraping when possible.
    """

    metadata = _METADATA

    def fetch_latest(self, limit: int = 20) -> list[RawArticle]:
        """Fetch latest articles from CoinDesk RSS."""
        retry_cfg = RetryConfig(max_attempts=3, base_delay=2.0, jitter=0.5)
        feed = retry_with_backoff(fetch_rss_feed, config=retry_cfg, feed_url=_METADATA.rss_url) or {}

        articles: list[RawArticle] = []
        for entry in feed.get("entries", [])[:limit]:
            article = self._parse_entry(_METADATA.display_name, entry)
            if article is not None:
                # Enrich content if RSS provided nothing meaningful
                article = enrich_article_content(article)
                articles.append(article)

        logger.info(
            "CoinDesk fetched %d raw entries (%d valid articles)",
            len(feed.get("entries", [])),
            len(articles),
        )
        return articles

    def _parse_entry(self, source_name: str, entry: dict[str, Any]) -> RawArticle | None:
        """Parse a single feedparser entry with CoinDesk-specific fallback."""
        article = entry_to_article(source_name, entry)
        if article is not None:
            # CoinDesk FeedBurner may omit content entirely.  If summary is also empty,
            # try to grab text from tags or leave it blank (not an error).
            if not article.summary and not article.content:
                tag_names = [t.get("term", "") for t in entry.get("tags", []) if isinstance(t, dict)]
                tags_str = " | ".join(tag_names)
                if tags_str:
                    return dc_replace(article, summary=f"[{tags_str}] No summary available in RSS feed.")
        return article


def fetch_latest(limit: int = 20) -> list[RawArticle]:
    """Convenience function for backward compatibility."""
    return CoinDeskAdapter().fetch_latest(limit=limit)
