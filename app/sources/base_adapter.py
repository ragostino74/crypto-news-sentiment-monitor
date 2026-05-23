"""Abstract base class and shared utilities for all source adapters.

Provides:
  - SourceAdapter ABC — every adapter must inherit from this, guaranteeing a
    uniform interface (fetch_latest + metadata).
  - RetryWithBackoff helper — automatic retry on transient HTTP failures.
  - RateLimiter — simple per-process delay between concurrent fetchers.
"""

from __future__ import annotations

import abc
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any

from .base import RawArticle, fetch_rss_feed, entry_to_article

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ ABC ---


@dataclass(frozen=True)
class SourceMetadata:
    """Immutable metadata describing a source adapter."""

    key: str             # unique slug, e.g. "coindesk"
    display_name: str    # human-readable name, e.g. "CoinDesk"
    rss_url: str         # primary RSS/Atom feed URL
    fallback_urls: list[str] = field(default_factory=list)  # secondary URLs


class SourceAdapter(abc.ABC):
    """Abstract base for all crypto news source adapters.

    Every adapter MUST:
      1. Inherit from SourceAdapter.
      2. Define a ``metadata`` class attribute (SourceMetadata).
      3. Implement ``fetch_latest(limit=20) -> list[RawArticle]``.

    The abstract method ``_parse_entry`` handles per-source quirks.
    Subclasses may also override ``_fetch_feed`` to use an alternative URL
    or a different transport (e.g. direct HTTP scraping).
    """

    metadata: SourceMetadata  # must be set by subclasses

    # ----------------------------------------------------------------- ABC ---

    @abc.abstractmethod
    def fetch_latest(self, limit: int = 20) -> list[RawArticle]:
        """Fetch the latest articles from this source.

        Args:
            limit: Maximum number of articles to return.

        Returns:
            List of RawArticle objects. Never raises — returns [] on error.
        """

    def _parse_entry(self, source_name: str, entry: dict[str, Any]) -> RawArticle | None:
        """Parse a single feedparser entry into a RawArticle.

        Override this in subclasses to apply per-source transformations.

        Args:
            source_name: Human-readable source name for the article record.
            entry: A dictionary from feedparser (one feed entry).

        Returns:
            A RawArticle, or None if the entry is malformed / should be skipped.
        """
        return entry_to_article(source_name, entry)

    # --------------------------------------------------------------- Helpers ---

    def _fetch_feed(self, url: str | None = None) -> Any:
        """Fetch and parse an RSS/Atom feed. Uses primary URL if *url* is None."""
        target = url or self.metadata.rss_url
        return fetch_rss_feed(target)


# ------------------------------------------------------------------ Retry ---


@dataclass
class RetryConfig:
    """Configuration for retry-with-exponential-backoff."""

    max_attempts: int = 3       # total attempts (1 original + retries)
    base_delay: float = 2.0     # initial delay in seconds
    max_delay: float = 30.0     # cap on back-off delay
    jitter: float = 0.5         # random jitter fraction [0, 1]

    # HTTP status codes that should trigger a retry (429 Too Many Requests, 5xx)
    retryable_status_codes: list[int] = field(
        default_factory=lambda: [429, 500, 502, 503, 504],
    )

    # Network exceptions that should trigger a retry
    retryable_exceptions: tuple[type[Exception], ...] = (
        ConnectionError,
        TimeoutError,
    )


def retry_with_backoff(func, config: RetryConfig | None = None, *args, **kwargs):
    """Execute *func* with exponential backoff on transient failures.

    Args:
        func: Callable to execute (usually ``fetch_rss_feed``).
        config: Retry configuration. Uses default if None.
        *args, **kwargs: Forwarded to *func*.

    Returns:
        The return value of *func* on success.
        Returns None after all attempts exhausted.
    """
    cfg = config or RetryConfig()
    last_error: Exception | None = None

    for attempt in range(1, cfg.max_attempts + 1):
        try:
            result = func(*args, **kwargs)
            if isinstance(result, dict) and "status" in result:
                status = result["status"]
                if status in cfg.retryable_status_codes and attempt < cfg.max_attempts:
                    delay = _calc_backoff(attempt, cfg.base_delay, cfg.max_delay, cfg.jitter)
                    logger.warning(
                        "%s returned HTTP %d — retrying in %.1fs (attempt %d/%d)",
                        kwargs.get("url", "unknown"), status, delay, attempt + 1, cfg.max_attempts,
                    )
                    time.sleep(delay)
                    continue
            return result
        except cfg.retryable_exceptions as exc:
            last_error = exc
            if attempt < cfg.max_attempts:
                delay = _calc_backoff(attempt, cfg.base_delay, cfg.max_delay, cfg.jitter)
                logger.warning(
                    "Connection error fetching feed — retrying in %.1fs (attempt %d/%d): %s",
                    delay, attempt + 1, cfg.max_attempts, exc,
                )
                time.sleep(delay)
            continue

    logger.error("All %d attempts exhausted for fetch: %s", cfg.max_attempts, last_error)
    return None


def _calc_backoff(attempt: int, base_delay: float, max_delay: float, jitter: float) -> float:
    """Calculate exponential back-off delay with jitter."""
    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
    jitter_amount = delay * jitter * random.uniform(0, 1)
    return delay + jitter_amount


# ------------------------------------------------------------------ Rate Limiter ---


class RateLimiter:
    """Simple token-bucket rate limiter for adapter fetches.

    Usage::

        limiter = RateLimiter(min_interval=2.0)   # at least 2s between calls
        ...
        with limiter.acquire():
            articles = source.fetch_latest()
    """

    def __init__(self, min_interval: float = 2.0):
        self.min_interval = min_interval
        self._last_call: float = 0.0
        self._lock = __import__("threading").Lock()

    def acquire(self) -> None:
        """Wait until the minimum interval has elapsed since the last call."""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_call
            if elapsed < self.min_interval:
                wait = self.min_interval - elapsed
                logger.debug("Rate limiter: waiting %.1fs", wait)
                time.sleep(wait)
            self._last_call = time.monotonic()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        pass


# ------------------------------------------------------------------ Article enrichment ---


def _try_extract_full_article(url: str, timeout: float = 15.0) -> str:
    """Attempt to extract readable article text from a URL using readability-lxml.

    Falls back gracefully if the library is not installed or extraction fails.

    Args:
        url: Article URL to scrape.
        timeout: HTTP timeout in seconds.

    Returns:
        Extracted plain-text body (up to 10 KB), or empty string on failure.
    """
    try:
        from readability import Document  # noqa: PLC0415
        import httpx  # noqa: PLC0415

        resp = httpx.get(url, timeout=timeout, follow_redirects=True)
        if resp.status_code != 200:
            return ""

        doc = Document(resp.text, url=url)
        html_content = doc.summary()

        # Strip HTML tags to get plain text
        import re  # noqa: PLC0415
        text = re.sub(r"<[^>]+>", " ", html_content).strip()
        text = re.sub(r"\s+", " ", text)

        return text[:10000]  # cap at 10 KB
    except ImportError:
        logger.debug(
            "readability-lxml not installed — skipping article enrichment. "
            "Install with: pip install readability-lxml",
        )
        return ""
    except Exception as exc:
        logger.warning("Article enrichment failed for %s: %s", url, exc)
        return ""


def enrich_article_content(article: RawArticle, timeout: float = 15.0) -> RawArticle:
    """Replace empty or very short content with scraped article text.

    Only triggers when both summary and content are below thresholds:
      - summary < 50 chars (or only contains fallback tags prefix)
      - content < 200 chars

    Args:
        article: The RawArticle to enrich.
        timeout: HTTP timeout for the enrichment fetch.

    Returns:
        A new RawArticle with enriched content, or the original if no enrichment needed.
    """
    # Skip if already has meaningful content
    if (article.content and len(article.content) >= 200) or \
       (article.summary and len(article.summary) >= 50):
        return article

    logger.debug(
        "Enriching %s/%s (summary=%d, content=%d chars)",
        article.source, article.title[:40],
        len(article.summary), len(article.content),
    )

    full_text = _try_extract_full_article(article.url, timeout)
    if not full_text:
        return article

    # Use full text as content (summary stays as-is from RSS)
    from .base import RawArticle
    return RawArticle(
        source=article.source,
        title=article.title,
        url=article.url,
        published_at=article.published_at,
        summary=article.summary or "",
        content=full_text,
    )
