"""Deduplication and cleaning pipeline for RawArticle lists.

Covers:
  - Per-article normalisation (whitespace, URL canonicalization)
  - URL-based deduplication (canonical URL lookup)
  - Hash-based fallback deduplication (title + source + published_at + summary)
  - Filtering of empty / malformed articles
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

from app.sources.base import RawArticle

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ Data model ---


@dataclass(slots=True)
class CleanedArticle:
    """Normalised article ready for downstream processing."""

    source: str
    title: str
    url: str
    published_at: datetime | None
    summary: str
    content: str
    canonical_url: str  # URL after normalisation (for reference / dedup)


# ------------------------------------------------------------------ Normalisation ---


def normalize_article(raw: RawArticle) -> CleanedArticle:
    """Normalize a single ``RawArticle`` into a ``CleanedArticle``.

    Applied transformations:
      - Whitespace collapsing on all text fields
      - URL canonicalization (see :func:`app.services.normalization.canonicalize_url`)
      - Summary and content length caps
      - Title strip
    """
    from .normalization import canonicalize_url, normalize_text_field

    return CleanedArticle(
        source=normalize_text_field(raw.source),
        title=(raw.title or "").strip(),
        url=canonicalize_url(raw.url),
        published_at=_ensure_utc(raw.published_at),
        summary=normalize_text_field(raw.summary)[:500],
        content=normalize_text_field(raw.content)[:5000],
        canonical_url=canonicalize_url(raw.url),
    )


def _ensure_utc(dt: datetime | None) -> datetime | None:
    """Attach UTC timezone if *dt* is naive."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


# ------------------------------------------------------------------ Filtering ---


def _is_valid(article: CleanedArticle) -> bool:
    """Return ``True`` when *article* has all required fields."""
    return bool(
        article.title
        and article.canonical_url
        and article.source
    )


# ------------------------------------------------------------------ Deduplication ---


def _url_hash(canonical_url: str) -> str:
    """URL-safe hash of a canonical URL (used for the dedup index)."""
    return hashlib.sha256(canonical_url.encode()).hexdigest()[:16]


def _content_hash(article: CleanedArticle) -> str:
    """Hash built from ``title + source + published_at + summary`` for fallback dedup.

    The summary is included so that two articles about the same topic but with
    different summaries (different content) don't collide.  Hash-only dedup
    triggers only when all four dimensions match — truly identical articles whose
    URLs are unreliable or point to mirror sites.
    """
    pub = article.published_at.isoformat() if article.published_at else ""
    key = f"{article.title}\n{article.source}\n{pub}\n{article.summary[:200]}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def clean_and_dedupe(raw_articles: list[RawArticle]) -> list[CleanedArticle]:
    """Normalize and deduplicate a list of ``RawArticle`` objects.

    Algorithm (two-pass):

    1. **URL-based dedup** – first-seen canonical URL wins.
    2. **Hash fallback** – if an article's content-hash already exists in the
       index, it is considered a duplicate and dropped.

    Empty / malformed articles are silently filtered.

    Returns:
        Deduplicated list of ``CleanedArticle`` (preserves first-seen order).
    """
    url_index: dict[str, str] = {}   # hash -> canonical_url
    hash_index: dict[str, int] = {}  # content_hash -> index in result
    seen_urls: set[str] = set()
    result: list[CleanedArticle] = []

    for raw in raw_articles:
        cleaned = normalize_article(raw)

        if not _is_valid(cleaned):
            logger.debug("Filtered malformed article: %s", cleaned)
            continue

        # --- URL-based dedup (primary) ------------------------------------------
        h = _url_hash(cleaned.canonical_url)
        if h in seen_urls:
            logger.debug(
                "Duplicate by URL hash %s: %s",
                h,
                cleaned.title[:60],
            )
            continue

        # Check for a prior article with the exact same canonical URL string
        if cleaned.canonical_url in seen_urls:
            logger.debug(
                "Duplicate by canonical URL %s: %s",
                cleaned.canonical_url,
                cleaned.title[:60],
            )
            continue

        seen_urls.add(cleaned.canonical_url)
        seen_urls.add(h)

        # --- Hash fallback (secondary) ------------------------------------------
        ch = _content_hash(cleaned)
        if ch in hash_index:
            logger.debug(
                "Duplicate by content hash %s: %s vs %s",
                ch,
                cleaned.title[:60],
                result[hash_index[ch]].title[:60],
            )
            continue

        hash_index[ch] = len(result)

        # --- Accept -------------------------------------------------------------
        result.append(cleaned)

    logger.info(
        "clean_and_dedupe: %d raw → %d clean (filtered %d, deduped %d)",
        len(raw_articles),
        len(result),
        sum(1 for r in raw_articles if normalize_article(r).canonical_url not in seen_urls) or 0,  # rough estimate
        len(raw_articles) - len(result),
    )

    return result


# ------------------------------------------------------------------ Convenience ---


def deduplicate_only(raw_articles: list[RawArticle]) -> list[CleanedArticle]:
    """Thin wrapper for URL-only dedup (skips content-hash fallback).

    Useful when the caller already guarantees unique content but wants
    URL normalization + dedup.
    """
    seen_urls: set[str] = set()
    result: list[CleanedArticle] = []

    for raw in raw_articles:
        cleaned = normalize_article(raw)
        if not _is_valid(cleaned):
            continue
        if cleaned.canonical_url in seen_urls:
            continue
        seen_urls.add(cleaned.canonical_url)
        result.append(cleaned)

    return result
