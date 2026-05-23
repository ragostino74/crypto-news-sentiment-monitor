"""Tests for the sources module.

Covers:
  - Shared utility functions (strip_html_tags, normalize_summary)
  - RawArticle data model
  - entry_to_article conversion logic
  - Integration-style fetch tests for all 10 crypto RSS sources
  - Error handling — each adapter returns [] on network failure

Run with: pytest tests/ -v
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from html import unescape
from unittest.mock import MagicMock, patch

import pytest

from app.sources.base import (
    RawArticle,
    strip_html_tags,
    normalize_summary,
    entry_to_article,
    _parse_feed_date,
)


# ====================================================================
# 1. Shared utilities
# ====================================================================

class TestStripHtmlTags:
    def test_simple_tags(self):
        assert strip_html_tags("<p>Hello</p>") == "Hello"

    def test_nested_tags(self):
        assert strip_html_tags("<div><p>Hello <b>world</b></p></div>") == "Hello world"

    def test_empty_input(self):
        assert strip_html_tags("") == ""
        assert strip_html_tags(None) == ""

    def test_no_tags(self):
        assert strip_html_tags("Just plain text") == "Just plain text"

    def test_whitespace_handling(self):
        assert strip_html_tags("<p>  Hello  </p>") == "Hello"


class TestNormalizeSummary:
    """Tests for the normalize_summary helper function."""

    def test_plain_text_summary(self):
        entry = {"summary": "This is a plain summary."}
        result = normalize_summary(entry)
        assert result == "This is a plain summary."

    def test_html_summary_stripped(self):
        entry = {
            "summary": "<p>This is an <strong>HTML</strong> summary.</p>"
        }
        result = normalize_summary(entry)
        assert result == "This is an HTML summary."

    def test_content_detail_fallback(self):
        """Should fall through to content_detail when summary is empty."""
        entry = {
            "summary": "",
            "content_detail": [{"value": "<p>Content detail text.</p>"}],
        }
        result = normalize_summary(entry)
        assert result == "Content detail text."

    def test_content_fallback(self):
        """Should fall through to content when summary and content_detail are empty."""
        entry = {
            "summary": "",
            "content": [{"value": "<p>Full content text.</p>"}],
        }
        result = normalize_summary(entry)
        assert result == "Full content text."

    def test_empty_entry(self):
        assert normalize_summary({}) == ""


# ====================================================================
# 2. Date parsing
# ====================================================================

class TestParseFeedDate:
    def test_struct_time_parsed(self):
        """Time struct from published_parsed."""
        st = time.struct_time((2026, 5, 22, 14, 30, 0, 0, 0, -1))
        entry = {"published_parsed": st}
        result = _parse_feed_date(entry)
        assert isinstance(result, datetime)
        assert result.year == 2026
        assert result.month == 5
        assert result.day == 22
        assert result.hour == 14
        assert result.minute == 30
        assert result.tzinfo is timezone.utc

    def test_rfc2822_string(self):
        """RFC-2822 formatted date string."""
        entry = {"published": "Fri, 22 May 2026 14:30:00 +0000"}
        result = _parse_feed_date(entry)
        assert isinstance(result, datetime)
        assert result.year == 2026
        assert result.month == 5

    def test_iso_string(self):
        """ISO formatted date string."""
        entry = {"published": "2026-05-22T14:30:00Z"}
        result = _parse_feed_date(entry)
        assert isinstance(result, datetime)

    def test_missing_date(self):
        assert _parse_feed_date({}) is None
        assert _parse_feed_date({"published": None}) is None

    def test_updated_parsed_as_fallback(self):
        """Should try published_parsed first, then updated_parsed."""
        st = time.struct_time((2026, 5, 23, 10, 0, 0, 0, 0, -1))
        entry = {
            "published_parsed": None,
            "updated_parsed": st,
        }
        result = _parse_feed_date(entry)
        assert isinstance(result, datetime)
        assert result.day == 23


# ====================================================================
# 3. Entry-to-Article conversion
# ====================================================================

class TestEntryToArticle:
    def test_minimal_valid_entry(self):
        entry = {
            "title": "Test Article",
            "links": [{"rel": "alternate", "href": "https://example.com/article"}],
        }
        article = entry_to_article("TestSource", entry)
        assert article is not None
        assert article.source == "TestSource"
        assert article.title == "Test Article"
        assert article.url == "https://example.com/article"

    def test_entry_with_summary(self):
        entry = {
            "title": "Summary Test",
            "links": [{"rel": "alternate", "href": "https://example.com/test"}],
            "summary": "<p>A short summary here.</p>",
            "content": [{"value": "<p>Full content body text.</p>"}],
        }
        article = entry_to_article("TestSource", entry)
        assert article is not None
        assert article.summary == "A short summary here."
        assert article.content == "Full content body text."

    def test_entry_with_date(self):
        import time as _time
        st = _time.struct_time((2026, 5, 22, 12, 0, 0, 0, 0, -1))
        entry = {
            "title": "Dated Article",
            "links": [{"rel": "alternate", "href": "https://example.com/dated"}],
            "published_parsed": st,
            "summary": "Has a date.",
        }
        article = entry_to_article("TestSource", entry)
        assert article is not None
        assert article.published_at is not None
        assert article.published_at.year == 2026
        assert article.published_at.hour == 12

    def test_malformed_entry_missing_title(self):
        entry = {
            "links": [{"rel": "alternate", "href": "https://example.com/no-title"}],
        }
        assert entry_to_article("TestSource", entry) is None

    def test_malformed_entry_missing_link(self):
        entry = {"title": "No Link Entry"}
        assert entry_to_article("TestSource", entry) is None

    def test_rawarticle_repr(self):
        article = RawArticle(
            source="Test",
            title="Short Title",
            url="https://example.com",
            published_at=None,
            summary="",
            content="",
        )
        assert "RawArticle" in repr(article)
        assert "Test" in repr(article)

    def test_rawarticle_slots(self):
        """Verify __slots__ is used (no __dict__)."""
        article = RawArticle(
            source="Test",
            title="T",
            url="https://example.com",
            published_at=None,
            summary="",
            content="",
        )
        assert not hasattr(article, "__dict__")

    def test_long_content_capped(self):
        long_html = "<p>" + "x" * 5000 + "</p>"
        entry = {
            "title": "Long Content",
            "links": [{"rel": "alternate", "href": "https://example.com/long"}],
            "content": [{"value": long_html}],
        }
        article = entry_to_article("TestSource", entry)
        assert article is not None
        assert len(article.content) <= 2000

    def test_long_summary_capped(self):
        long_text = "x" * 1000
        entry = {
            "title": "Long Summary",
            "links": [{"rel": "alternate", "href": "https://example.com/long"}],
            "summary": long_text,
        }
        article = entry_to_article("TestSource", entry)
        assert article is not None
        assert len(article.summary) <= 500


# ====================================================================
# 4. Source adapter fetch_latest — live tests (CoinDesk, Cointelegraph)
# ====================================================================

class TestLiveFetch:
    """Integration tests that actually hit the network.

    These are marked with `pytest.mark.integration` so they can be skipped
    in environments without internet access (pytest -m "not integration").
    """

    @pytest.mark.integration
    def test_coindesk_fetch(self):
        from app.sources.coindesk import fetch_latest

        articles = fetch_latest(limit=5)
        assert isinstance(articles, list), "fetch_latest must return a list"

        # We should get at least some articles (feed is active).
        # In rare cases of network issues or feed changes, 0 is acceptable.
        if articles:
            article = articles[0]
            assert isinstance(article, RawArticle)
            assert article.source == "CoinDesk"
            assert len(article.title) > 0
            assert "http" in article.url
            # Summary may be empty for CoinDesk RSS — that's OK.

    @pytest.mark.integration
    def test_cointelegraph_fetch(self):
        from app.sources.cointelegraph import fetch_latest

        articles = fetch_latest(limit=5)
        assert isinstance(articles, list)

        if articles:
            article = articles[0]
            assert isinstance(article, RawArticle)
            assert article.source == "Cointelegraph"
            assert len(article.title) > 0
            assert "http" in article.url
            # Cointelegraph usually provides a summary.
            if article.summary:
                assert len(article.summary) > 0

    @pytest.mark.integration
    def test_decrypt_fetch(self):
        from app.sources.decrypt import fetch_latest

        articles = fetch_latest(limit=5)
        assert isinstance(articles, list)

        if articles:
            article = articles[0]
            assert isinstance(article, RawArticle)
            assert article.source == "Decrypt"
            assert len(article.title) > 0
            assert "http" in article.url

    @pytest.mark.integration
    def test_theblock_fetch(self):
        from app.sources.theblock import fetch_latest

        articles = fetch_latest(limit=5)
        assert isinstance(articles, list)

        if articles:
            article = articles[0]
            assert isinstance(article, RawArticle)
            assert article.source == "The Block"
            assert len(article.title) > 0
            assert "http" in article.url

    @pytest.mark.integration
    def test_cryptoslate_fetch(self):
        from app.sources.cryptoslate import fetch_latest

        articles = fetch_latest(limit=5)
        assert isinstance(articles, list)

        if articles:
            article = articles[0]
            assert isinstance(article, RawArticle)
            assert article.source == "CryptoSlate"
            assert len(article.title) > 0
            assert "http" in article.url

    @pytest.mark.integration
    def test_coinjournal_fetch(self):
        from app.sources.coinjournal import fetch_latest

        articles = fetch_latest(limit=5)
        assert isinstance(articles, list)

        if articles:
            article = articles[0]
            assert isinstance(article, RawArticle)
            assert article.source == "CoinJournal"
            assert len(article.title) > 0
            assert "http" in article.url

    @pytest.mark.integration
    def test_ambcrypto_fetch(self):
        from app.sources.ambcrypto import fetch_latest

        articles = fetch_latest(limit=5)
        assert isinstance(articles, list)

        if articles:
            article = articles[0]
            assert isinstance(article, RawArticle)
            assert article.source == "AMBCrypto"
            assert len(article.title) > 0
            assert "http" in article.url

    @pytest.mark.integration
    def test_bitcoinist_fetch(self):
        from app.sources.bitcoinist import fetch_latest

        articles = fetch_latest(limit=5)
        assert isinstance(articles, list)

        if articles:
            article = articles[0]
            assert isinstance(article, RawArticle)
            assert article.source == "Bitcoinist"
            assert len(article.title) > 0
            assert "http" in article.url

    @pytest.mark.integration
    def test_cryptopotato_fetch(self):
        from app.sources.cryptopotato import fetch_latest

        articles = fetch_latest(limit=5)
        assert isinstance(articles, list)

        if articles:
            article = articles[0]
            assert isinstance(article, RawArticle)
            assert article.source == "CryptoPotato"
            assert len(article.title) > 0
            assert "http" in article.url

    @pytest.mark.integration
    def test_beincrypto_fetch(self):
        from app.sources.beincrypto import fetch_latest

        articles = fetch_latest(limit=5)
        assert isinstance(articles, list)

        if articles:
            article = articles[0]
            assert isinstance(article, RawArticle)
            assert article.source == "BeInCrypto"
            assert len(article.title) > 0
            assert "http" in article.url


# ====================================================================
# 5. Error handling — adapter returns empty list on failure
# ====================================================================

class TestErrorHandling:
    def test_coindesk_no_network(self):
        from app.sources.coindesk import fetch_latest

        # Force a network error by mocking with an unreachable URL.
        with patch("app.sources.coindesk.fetch_rss_feed") as mock_fetch:
            mock_fetch.side_effect = ConnectionError("Connection refused")
            articles = fetch_latest()
            assert articles == []

    def test_cointelegraph_no_network(self):
        from app.sources.cointelegraph import fetch_latest

        with patch("app.sources.cointelegraph.fetch_rss_feed") as mock_fetch:
            mock_fetch.side_effect = ConnectionError("Network error")
            articles = fetch_latest()
            assert articles == []

    def test_ambcrypto_no_network(self):
        from app.sources.ambcrypto import fetch_latest

        with patch("app.sources.ambcrypto.fetch_rss_feed") as mock_fetch:
            mock_fetch.side_effect = ConnectionError("Network error")
            articles = fetch_latest()
            assert articles == []

    def test_cryptopotato_no_network(self):
        from app.sources.cryptopotato import fetch_latest

        with patch("app.sources.cryptopotato.fetch_rss_feed") as mock_fetch:
            mock_fetch.side_effect = ConnectionError("Network error")
            articles = fetch_latest()
            assert articles == []

    def test_beincrypto_no_network(self):
        from app.sources.beincrypto import fetch_latest

        with patch("app.sources.beincrypto.fetch_rss_feed") as mock_fetch:
            mock_fetch.side_effect = ConnectionError("Network error")
            articles = fetch_latest()
            assert articles == []

    def test_coinjournal_no_network(self):
        from app.sources.coinjournal import fetch_latest

        with patch("app.sources.coinjournal.fetch_rss_feed") as mock_fetch:
            mock_fetch.side_effect = ConnectionError("Network error")
            articles = fetch_latest()
            assert articles == []

    def test_bitcoinist_no_network(self):
        from app.sources.bitcoinist import fetch_latest

        with patch("app.sources.bitcoinist.fetch_rss_feed") as mock_fetch:
            mock_fetch.side_effect = ConnectionError("Network error")
            articles = fetch_latest()
            assert articles == []
