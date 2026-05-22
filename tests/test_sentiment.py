"""Tests for app.services.sentiment module.

Covers:
  - build_sentiment_text (priority, length capping)
  - VADER classification positive / neutral / negative
  - analyze_sentiment per-article
  - aggregate_sentiment global stats
  - Empty / edge-case handling
"""

from __future__ import annotations

import pytest

from app.services.sentiment import (
    SentimentResult,
    VaderSentimentEngine,
    build_sentiment_text,
    classify_vader,
    analyze_sentiment,
    aggregate_sentiment,
)


# ====================================================================
# Fixtures
# ====================================================================

@pytest.fixture()
def engine():
    """Return a VaderSentimentEngine instance."""
    return VaderSentimentEngine()


class MockArticle:
    """Thin duck-type for CleanedArticle / RawArticle (no dataclasses needed)."""

    def __init__(self, title="", summary="", content=""):
        self.title = title
        self.summary = summary
        self.content = content


# ====================================================================
# 1. build_sentiment_text
# ====================================================================

class TestBuildSentimentText:
    def test_title_only(self):
        text = build_sentiment_text("Bitcoin soars to new heights")
        assert text == "Bitcoin soars to new heights"

    def test_title_plus_summary(self):
        text = build_sentiment_text(
            "Ethereum drops", summary="ETH falls below key support"
        )
        assert text.startswith("Ethereum drops")
        assert "ETH falls" in text

    def test_excerpt_included(self):
        text = build_sentiment_text(
            "Market crash fears", content="Analysts warn of a potential selloff."
        )
        assert "Market crash fears" in text
        assert "selloff" in text

    def test_punctuation_preserved(self):
        """VADER relies on punctuation — it must survive."""
        text = build_sentiment_text("Bitcoin is amazing!!!")
        assert "!!!" in text

    def test_caps_preserved(self):
        """ALL CAPS is a VADER signal."""
        text = build_sentiment_text("BITCOIN PLUMMETS TO ASHES")
        assert text == "BITCOIN PLUMMETS TO ASHES"

    def test_length_capping(self):
        long_title = "A" * 2000
        text = build_sentiment_text(long_title)
        assert len(text) <= 1200  # default max_len

    def test_empty_input(self):
        text = build_sentiment_text("")
        assert text == ""

    def test_none_fields(self):
        text = build_sentiment_text("")
        assert text == ""


# ====================================================================
# 2. VADER classification (positive / neutral / negative)
# ====================================================================

class TestVaderClassification:
    def test_positive_compound(self, engine):
        """Strong positive → 'positive'."""
        result = engine.classify(0.8542)
        assert result == "positive"

    def test_positive_boundary(self, engine):
        """Exactly at threshold → 'positive'."""
        assert engine.classify(0.05) == "positive"

    def test_negative_compound(self, engine):
        """Strong negative → 'negative'."""
        result = engine.classify(-0.7623)
        assert result == "negative"

    def test_negative_boundary(self, engine):
        """Exactly at threshold → 'negative'."""
        assert engine.classify(-0.05) == "negative"

    def test_neutral_compound(self, engine):
        """Zero compound → 'neutral'."""
        assert engine.classify(0.0) == "neutral"

    def test_neutral_boundary_pos(self, engine):
        """Just below positive threshold → 'neutral'."""
        assert engine.classify(0.0499) == "neutral"

    def test_neutral_boundary_neg(self, engine):
        """Just above negative threshold → 'neutral'."""
        assert engine.classify(-0.0499) == "neutral"


class TestClassifyVader:
    """Standalone classify_vader convenience function."""

    def test_positive(self):
        assert classify_vader(0.5) == "positive"

    def test_negative(self):
        assert classify_vader(-0.5) == "negative"

    def test_neutral(self):
        assert classify_vader(0.01) == "neutral"


# ====================================================================
# 3. analyze_sentiment (per-article)
# ====================================================================

class TestAnalyzeSentiment:
    def _make_article(self, **kwargs):
        defaults = {"title": "Bitcoin goes up", "summary": "", "content": ""}
        defaults.update(kwargs)
        return MockArticle(**defaults)

    def test_positive_article(self):
        """Clearly positive headline → positive result."""
        article = self._make_article(
            title="Bitcoin surges past $100,000 to new all-time high!",
            summary="Investors celebrate as BTC breaks records.",
        )
        result, text = analyze_sentiment(article)

        assert result.sentiment_label == "positive"
        assert result.sentiment_engine == "vader"
        assert result.sentiment_compound > 0.05
        assert result.sentiment_pos > result.sentiment_neg

    def test_negative_article(self):
        """Clearly negative headline → negative result."""
        article = self._make_article(
            title="Crypto market CRASHES: billions wiped out in panic selling",
            summary="Bitcoin drops below critical support, traders lose millions.",
        )
        result, text = analyze_sentiment(article)

        assert result.sentiment_label == "negative"
        assert result.sentiment_engine == "vader"
        assert result.sentiment_compound < -0.05
        assert result.sentiment_neg > result.sentiment_pos

    def test_neutral_article(self):
        """Factual, neutral headline → neutral result."""
        article = self._make_article(
            title="Bitcoin price trades in narrow range today",
            summary="BTC consolidates between $60,000 and $61,000.",
        )
        result, text = analyze_sentiment(article)

        assert result.sentiment_label == "neutral"
        assert abs(result.sentiment_compound) < 0.05

    def test_empty_article(self):
        """No content → neutral baseline."""
        article = self._make_article(title="", summary="", content="")
        result, text = analyze_sentiment(article)

        assert result.sentiment_label == "neutral"
        assert result.sentiment_compound == 0.0
        assert result.sentiment_neu == 1.0
        assert result.sentiment_pos == 0.0
        assert result.sentiment_neg == 0.0

    def test_title_priority_over_content(self):
        """Title sentiment should dominate when content is neutral."""
        article = self._make_article(
            title="Bitcoin is amazing!!!",
            summary="",
            content="The price is currently at 60000 dollars.",
        )
        result, text = analyze_sentiment(article)

        assert result.sentiment_label == "positive"

    def test_result_has_all_fields(self):
        article = self._make_article(title="Market rises")
        result, _ = analyze_sentiment(article)

        assert hasattr(result, "sentiment_neg")
        assert hasattr(result, "sentiment_neu")
        assert hasattr(result, "sentiment_pos")
        assert hasattr(result, "sentiment_compound")
        assert hasattr(result, "sentiment_label")
        assert hasattr(result, "sentiment_engine")

    def test_scores_sum_to_one(self):
        """pos + neu + neg should be very close to 1.0."""
        article = self._make_article(title="Bitcoin is amazing!!!")
        result, _ = analyze_sentiment(article)

        total = result.sentiment_pos + result.sentiment_neu + result.sentiment_neg
        assert abs(total - 1.0) < 0.01


# ====================================================================
# 4. aggregate_sentiment
# ====================================================================

class TestAggregateSentiment:
    def _make_result(self, compound=0.0, label="neutral"):
        return SentimentResult(
            sentiment_neg=0.1,
            sentiment_neu=0.5,
            sentiment_pos=0.4,
            sentiment_compound=compound,
            sentiment_label=label,
        )

    def test_empty_list(self):
        agg = aggregate_sentiment([])

        assert agg["total"] == 0
        assert agg["mean_compound"] == 0.0
        assert agg["global_label"] == "neutral"
        assert agg["count_positive"] == 0
        assert agg["count_neutral"] == 0
        assert agg["count_negative"] == 0

    def test_all_positive(self):
        results = [
            self._make_result(compound=0.5, label="positive"),
            self._make_result(compound=0.6, label="positive"),
        ]
        agg = aggregate_sentiment(results)

        assert agg["total"] == 2
        assert agg["mean_compound"] == pytest.approx(0.55)
        assert agg["global_label"] == "positive"
        assert agg["count_positive"] == 2
        assert agg["count_negative"] == 0

    def test_all_negative(self):
        results = [
            self._make_result(compound=-0.7, label="negative"),
            self._make_result(compound=-0.3, label="negative"),
        ]
        agg = aggregate_sentiment(results)

        assert agg["total"] == 2
        assert agg["mean_compound"] == pytest.approx(-0.5)
        assert agg["global_label"] == "negative"
        assert agg["count_negative"] == 2

    def test_mixed_labels(self):
        results = [
            self._make_result(compound=0.5, label="positive"),
            self._make_result(compound=-0.5, label="negative"),
            self._make_result(compound=0.01, label="neutral"),
            self._make_result(compound=0.3, label="positive"),
        ]
        agg = aggregate_sentiment(results)

        assert agg["total"] == 4
        assert agg["mean_compound"] == pytest.approx(0.0775)
        assert agg["global_label"] == "positive"  # most frequent
        assert agg["count_positive"] == 2
        assert agg["count_neutral"] == 1
        assert agg["count_negative"] == 1

    def test_mean_compound_precision(self):
        """Mean should be rounded to 6 decimal places."""
        results = [self._make_result(compound=0.33333333)] * 3
        agg = aggregate_sentiment(results)
        assert str(agg["mean_compound"]) == "0.333333"

    def test_tie_breaks_first_occurrence(self):
        """When positive and negative tie, first class encountered in dict order."""
        results = [
            self._make_result(compound=0.5, label="positive"),
            self._make_result(compound=-0.5, label="negative"),
        ]
        agg = aggregate_sentiment(results)

        # Both have count 1; max() returns the first one found in iteration.
        # dict insertion order is positive → negative, so "positive" wins.
        assert agg["global_label"] == "positive"


# ====================================================================
# 5. SentimentResult dataclass
# ====================================================================

class TestSentimentResult:
    def test_defaults(self):
        r = SentimentResult(
            sentiment_neg=0.1,
            sentiment_neu=0.3,
            sentiment_pos=0.6,
            sentiment_compound=0.5,
            sentiment_label="positive",
        )
        assert r.sentiment_engine == "vader"

    def test_custom_engine(self):
        r = SentimentResult(
            sentiment_neg=0.0,
            sentiment_neu=1.0,
            sentiment_pos=0.0,
            sentiment_compound=0.0,
            sentiment_label="neutral",
            sentiment_engine="test-engine",
        )
        assert r.sentiment_engine == "test-engine"
