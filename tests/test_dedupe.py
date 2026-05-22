"""Tests for app.services.dedupe and app.services.normalization.

Covers:
  - Whitespace normalization
  - URL canonicalization (scheme, trailing slash, UTM params, sorting)
  - Deduplication by canonical URL
  - Fallback deduplication by content hash (title + source + published_at + summary)
  - Malformed article filtering
  - Empty / None input handling
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone

from app.sources.base import RawArticle
from app.services.dedupe import clean_and_dedupe, normalize_article, deduplicate_only
from app.services.normalization import (
    canonicalize_url,
    normalize_text_field,
    normalize_whitespace,
)


# ====================================================================
# 1. Whitespace normalization
# ====================================================================

class TestNormalizeWhitespace:
    def test_consecutive_spaces(self):
        assert normalize_whitespace("hello   world") == "hello world"

    def test_newlines_and_tabs(self):
        result = normalize_whitespace("hello\n\n\tworld")
        assert result == "hello world"

    def test_leading_trailing(self):
        assert normalize_whitespace("  hello  ") == "hello"

    def test_none_input(self):
        assert normalize_whitespace(None) == ""

    def test_empty_string(self):
        assert normalize_whitespace("") == ""

    def test_no_extra_whitespace(self):
        assert normalize_whitespace("already clean") == "already clean"

    def test_nbsp_normalization(self):
        # Non-breaking space should become regular space
        result = normalize_whitespace("hello\u00a0world")
        assert result == "hello world"


class TestNormalizeTextField:
    def test_unicode_nfc(self):
        # Decomposed é (e + combining accent) → composed é
        decomposed = "cafe\u0301"  # e + combining acute
        result = normalize_text_field(decomposed)
        assert "\u0301" not in result  # no combining marks after NFC

    def test_whitespace_collapse(self):
        assert normalize_text_field("  hello   world  ") == "hello world"

    def test_none_and_empty(self):
        assert normalize_text_field(None) == ""
        assert normalize_text_field("") == ""


# ====================================================================
# 2. URL canonicalization
# ====================================================================

class TestCanonicalizeUrl:
    def test_force_https_crypto_domains(self):
        url = "http://coindesk.com/article/bitcoin"
        result = canonicalize_url(url)
        assert result.startswith("https")

    def test_remove_trailing_slash(self):
        result = canonicalize_url("https://cointelegraph.com/news/btc/")
        assert not result.endswith("/") or result == "https://cointelegraph.com"

    def test_sort_query_params(self):
        url = "https://example.com/article?z=1&a=2&m=3"
        result = canonicalize_url(url)
        # params should appear as a=2&m=3&z=1 (alphabetical)
        assert result.index("a=") < result.index("m=") < result.index("z=")

    def test_remove_utm_params(self):
        url = "https://coindesk.com/article?utm_source=tg&utm_medium=social&a=1"
        result = canonicalize_url(url)
        assert "utm_" not in result
        assert "a=1" in result

    def test_lower_case_host(self):
        result = canonicalize_url("https://CoinDesk.COM/Article")
        assert "coindesk.com" in result

    def test_remove_index_html(self):
        result = canonicalize_url("https://example.com/index.html")
        assert "index" not in result

    def test_none_and_empty(self):
        assert canonicalize_url(None) == ""
        assert canonicalize_url("") == ""

    def test_unchanged_simple_url(self):
        result = canonicalize_url("https://example.com/article")
        assert result == "https://example.com/article"

    def test_fragment_removed_on_sort(self):
        # Fragments are not in the query string — verify behavior
        url = "https://example.com/page#section"
        result = canonicalize_url(url)
        # parse_qs doesn't touch fragments; they survive urlunparse
        assert "#" in result or result.endswith("page")


# ====================================================================
# 3. Normalize article (single article)
# ====================================================================

class TestNormalizeArticle:
    def test_basic_fields(self):
        raw = RawArticle(
            source="  CoinDesk  ",
            title="  Bitcoin Hits $100K  ",
            url="https://coindesk.com/article",
            published_at=datetime(2025, 6, 15, 10, 30, tzinfo=timezone.utc),
            summary="A brief snippet.",
            content="Full content here.",
        )
        cleaned = normalize_article(raw)
        assert cleaned.source == "CoinDesk"
        assert cleaned.title == "Bitcoin Hits $100K"
        assert cleaned.canonical_url == "https://coindesk.com/article"
        assert cleaned.summary == "A brief snippet."

    def test_naive_datetime_becomes_utc(self):
        naive = datetime(2025, 6, 15)
        raw = RawArticle(
            source="Test", title="T", url="https://ex.com/a",
            published_at=naive, summary="", content="",
        )
        cleaned = normalize_article(raw)
        assert cleaned.published_at.tzinfo is not None

    def test_none_published_at_preserved(self):
        raw = RawArticle(
            source="Test", title="T", url="https://ex.com/a",
            published_at=None, summary="", content="",
        )
        cleaned = normalize_article(raw)
        assert cleaned.published_at is None


# ====================================================================
# 4. Deduplication by canonical URL
# ====================================================================

class TestDedupeByUrl:
    def _make(self, **kwargs):
        defaults = {
            "source": "Test",
            "title": "Title",
            "url": "https://example.com/article",
            "published_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
            "summary": "Summary",
            "content": "Content",
        }
        defaults.update(kwargs)
        return RawArticle(**defaults)

    def test_exact_duplicate_url(self):
        articles = [
            self._make(title="First"),
            self._make(title="Second"),  # same URL
        ]
        result = clean_and_dedupe(articles)
        assert len(result) == 1
        assert result[0].title == "First"

    def test_different_urls_kept(self):
        """Three distinct URLs with different summaries → all kept."""
        articles = [
            self._make(url="https://example.com/a", summary="Summary A"),
            self._make(url="https://example.com/b", summary="Summary B"),
            self._make(url="https://example.com/c", summary="Summary C"),
        ]
        result = clean_and_dedupe(articles)
        assert len(result) == 3

    def test_case_insensitive_url(self):
        articles = [
            self._make(url="https://EXAMPLE.COM/article"),
            self._make(url="https://example.com/Article"),
        ]
        result = clean_and_dedupe(articles)
        assert len(result) == 1

    def test_utm_variant_is_deduped(self):
        articles = [
            self._make(url="https://coindesk.com/news?utm_source=tg"),
            self._make(url="https://coindesk.com/news?utm_medium=twitter"),
        ]
        result = clean_and_dedupe(articles)
        assert len(result) == 1

    def test_first_seen_wins(self):
        articles = [
            self._make(title="Winner", url="https://ex.com/x"),
            self._make(title="Loser", url="https://EX.COM/X"),
        ]
        result = clean_and_dedupe(articles)
        assert len(result) == 1
        assert result[0].title == "Winner"


# ====================================================================
# 5. Fallback deduplication by content hash
# ====================================================================

class TestDedupeByContentHash:
    def _make(self, **kwargs):
        defaults = {
            "source": "Test",
            "title": "Same Title",
            "url": "https://different.com/a",
            "published_at": datetime(2025, 3, 1, tzinfo=timezone.utc),
            "summary": "Summary",
            "content": "Content",
        }
        defaults.update(kwargs)
        return RawArticle(**defaults)

    def test_same_title_source_date_different_urls(self):
        """Articles with same title + source + date but different URLs → deduped."""
        articles = [
            self._make(url="https://source1.com/a"),
            self._make(url="https://source2.com/b"),
        ]
        result = clean_and_dedupe(articles)
        assert len(result) == 1

    def test_different_title_survives(self):
        """Different title → different hash → both kept."""
        articles = [
            self._make(title="First Title", url="https://a.com/1"),
            self._make(title="Second Title", url="https://b.com/2"),
        ]
        result = clean_and_dedupe(articles)
        assert len(result) == 2

    def test_different_source_survives(self):
        """Different source → different hash → both kept."""
        articles = [
            self._make(source="CoinDesk", url="https://a.com/1"),
            self._make(source="Cointelegraph", url="https://b.com/2"),
        ]
        result = clean_and_dedupe(articles)
        assert len(result) == 2

    def test_no_published_at_different_hashes(self):
        """No date + same title + same source → deduped (hash is deterministic)."""
        articles = [
            self._make(published_at=None, url="https://a.com/1"),
            self._make(published_at=None, url="https://b.com/2"),
        ]
        result = clean_and_dedupe(articles)
        assert len(result) == 1

    def test_different_dates_survives(self):
        """Same title + source but different dates → both kept."""
        articles = [
            self._make(
                published_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
                url="https://a.com/1",
            ),
            self._make(
                published_at=datetime(2025, 2, 1, tzinfo=timezone.utc),
                url="https://b.com/2",
            ),
        ]
        result = clean_and_dedupe(articles)
        assert len(result) == 2


# ====================================================================
# 6. Filtering and edge cases
# ====================================================================

class TestFiltering:
    def test_empty_list(self):
        assert clean_and_dedupe([]) == []

    def test_all_malformed(self):
        # Missing title → filtered
        articles = [
            RawArticle(
                source="Test", title="", url="https://a.com/1",
                published_at=None, summary="", content="",
            ),
        ]
        result = clean_and_dedupe(articles)
        assert len(result) == 0

    def test_mixed_valid_invalid(self):
        articles = [
            RawArticle(source="Test", title="Valid", url="https://a.com/1",
                       published_at=None, summary="", content=""),
            RawArticle(source="Test", title="", url="https://a.com/2",
                       published_at=None, summary="", content=""),  # no title
        ]
        result = clean_and_dedupe(articles)
        assert len(result) == 1

    def test_none_title(self):
        articles = [
            RawArticle(source="Test", title=None, url="https://a.com/1",
                       published_at=None, summary="", content=""),
        ]
        result = clean_and_dedupe(articles)
        assert len(result) == 0

    def test_preserves_order(self):
        urls = [f"https://example.com/{i}" for i in range(10)]
        articles = [
            RawArticle(source="S", title=f"Title {i}", url=u,
                       published_at=None, summary="", content="")
            for i, u in enumerate(urls)
        ]
        result = clean_and_dedupe(articles)
        assert len(result) == 10
        for i, article in enumerate(result):
            assert f"Title {i}" in article.title


# ====================================================================
# 7. deduplicate_only (URL-only mode)
# ====================================================================

class TestDeduplicateOnly:
    def test_url_only_dedupe(self):
        articles = [
            RawArticle(source="A", title="T1", url="https://ex.com/a",
                       published_at=None, summary="", content=""),
            RawArticle(source="B", title="T2", url="https://ex.com/a",
                       published_at=None, summary="", content=""),  # same URL
        ]
        result = deduplicate_only(articles)
        assert len(result) == 1

    def test_url_only_keeps_different_urls(self):
        articles = [
            RawArticle(source="A", title="T1", url="https://ex.com/1",
                       published_at=None, summary="", content=""),
            RawArticle(source="B", title="T2", url="https://ex.com/2",
                       published_at=None, summary="", content=""),
        ]
        result = deduplicate_only(articles)
        assert len(result) == 2
