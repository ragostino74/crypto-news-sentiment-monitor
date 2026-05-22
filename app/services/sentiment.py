"""Sentiment analysis module for cleaned articles.

Covers:
  - VADER-based sentiment classification (positive / neutral / negative)
  - Text building from article fields (title + summary + content excerpt)
  - Per-article and global aggregation of sentiment scores
  - Pluggable engine interface via Protocol
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Protocol

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ Constants ---

_SENTIMENT_TEXT_MAX = 1200  # chars cap for the composite sentiment text


# ------------------------------------------------------------------ Data model ---


@dataclasses.dataclass(slots=True)
class SentimentResult:
    """Sentiment analysis result for a single article."""

    sentiment_neg: float
    sentiment_neu: float
    sentiment_pos: float
    sentiment_compound: float
    sentiment_label: str  # "positive" | "neutral" | "negative"
    sentiment_engine: str = "vader"


# ------------------------------------------------------------------ Protocol ---


class SentimentEngine(Protocol):
    """Interface for pluggable sentiment analysers."""

    def analyze(self, text: str) -> SentimentResult: ...  # noqa: D102


# ------------------------------------------------------------------ Text builder ---


def build_sentiment_text(
    title: str,
    summary: str = "",
    content: str = "",
    max_len: int = _SENTIMENT_TEXT_MAX,
) -> str:
    """Build a composite text from article fields for VADER analysis.

    Priority order:
      1. Title (headline carries strong sentiment signals)
      2. Summary
      3. Short excerpt of content (first ~300 chars)

    Punctuation and capitalisation are intentionally preserved — VADER uses them
    as key signals (!, ALL CAPS).
    """
    parts: list[str] = []

    if title:
        parts.append(title)

    if summary:
        parts.append(summary)

    # Content excerpt — keep it short to avoid diluting headline sentiment.
    if content:
        # Strip trailing whitespace but preserve internal structure
        excerpt = content.strip()[:300]
        if excerpt:
            parts.append(excerpt)

    composite = " ".join(parts)

    # Hard cap on total length.
    if len(composite) > max_len:
        composite = composite[:max_len]

    return composite


# ------------------------------------------------------------------ VADER engine ---


# Module-level singleton — initialised on first use.
_VADER_ANALYZER: object | None = None


def _get_vader_analyzer():
    """Return a cached SentimentIntensityAnalyzer, loading VADER lexicons on demand."""
    global _VADER_ANALYZER
    if _VADER_ANALYZER is None:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer  # noqa: PLC0414

        _VADER_ANALYZER = SentimentIntensityAnalyzer()
    return _VADER_ANALYZER


class VaderSentimentEngine:
    """VADER-based sentiment analysis engine.

    Uses the compound score with standard thresholds:
      - compound >= 0.05 → positive
      - compound <= -0.05 → negative
      - otherwise         → neutral
    """

    LABEL_POSITIVE = "positive"
    LABEL_NEUTRAL = "neutral"
    LABEL_NEGATIVE = "negative"

    THRESHOLD_POS = 0.05
    THRESHOLD_NEG = -0.05

    engine_name = "vader"

    def analyze(self, text: str) -> SentimentResult:
        """Analyse *text* and return a ``SentimentResult``."""
        scores = _get_vader_analyzer().polarity_scores(text)
        label = self.classify(scores["compound"])

        return SentimentResult(
            sentiment_neg=scores["neg"],
            sentiment_neu=scores["neu"],
            sentiment_pos=scores["pos"],
            sentiment_compound=scores["compound"],
            sentiment_label=label,
            sentiment_engine=self.engine_name,
        )

    def classify(self, compound: float) -> str:
        """Classify a single compound score into a label."""
        if compound >= self.THRESHOLD_POS:
            return self.LABEL_POSITIVE
        if compound <= self.THRESHOLD_NEG:
            return self.LABEL_NEGATIVE
        return self.LABEL_NEUTRAL


# ------------------------------------------------------------------ Convenience functions ---


def classify_vader(compound: float) -> str:
    """One-shot VADER classification using standard thresholds.

    Shortcut for callers that already have a compound score.
    """
    engine = VaderSentimentEngine()
    return engine.classify(compound)


def analyze_sentiment(
    article,  # CleanedArticle (duck-type: source/title/url/summary/content)
    *,
    engine: SentimentEngine | None = None,
    max_text_len: int = _SENTIMENT_TEXT_MAX,
) -> tuple[SentimentResult, str]:
    """Analyse a single cleaned article's sentiment.

    Returns:
        ``(result, composite_text)`` — the ``SentimentResult`` and the text that
        was actually fed to the analyzer (useful for debugging).
    """
    if engine is None:
        engine = VaderSentimentEngine()

    composite = build_sentiment_text(
        title=article.title or "",
        summary=getattr(article, "summary", ""),
        content=getattr(article, "content", ""),
        max_len=max_text_len,
    )

    if not composite:
        # No text to analyse — return neutral baseline.
        result = SentimentResult(
            sentiment_neg=0.0,
            sentiment_neu=1.0,
            sentiment_pos=0.0,
            sentiment_compound=0.0,
            sentiment_label=VaderSentimentEngine.LABEL_NEUTRAL,
        )
    else:
        result = engine.analyze(composite)

    return result, composite


def aggregate_sentiment(
    results: list[SentimentResult],
) -> dict:
    """Aggregate a list of ``SentimentResult`` into summary statistics.

    Returns a dict with:
      - ``mean_compound``: mean compound score across all articles (0 if empty)
      - ``global_label``: dominant label ("positive", "neutral", "negative")
      - ``count_positive``, ``count_neutral``, ``count_negative``
      - ``total``: number of results processed
    """
    total = len(results)

    if total == 0:
        return {
            "mean_compound": 0.0,
            "global_label": "neutral",
            "count_positive": 0,
            "count_neutral": 0,
            "count_negative": 0,
            "total": 0,
        }

    mean_compound = sum(r.sentiment_compound for r in results) / total

    counts: dict[str, int] = {
        VaderSentimentEngine.LABEL_POSITIVE: 0,
        VaderSentimentEngine.LABEL_NEUTRAL: 0,
        VaderSentimentEngine.LABEL_NEGATIVE: 0,
    }
    for r in results:
        label = r.sentiment_label
        counts[label] = counts.get(label, 0) + 1

    # Global label = the most common class.
    global_label = max(counts, key=lambda k: counts[k])  # type: ignore[arg-type]

    return {
        "mean_compound": round(mean_compound, 6),
        "global_label": global_label,
        "count_positive": counts[VaderSentimentEngine.LABEL_POSITIVE],
        "count_neutral": counts[VaderSentimentEngine.LABEL_NEUTRAL],
        "count_negative": counts[VaderSentimentEngine.LABEL_NEGATIVE],
        "total": total,
    }
