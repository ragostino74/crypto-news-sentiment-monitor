"""Sentiment analysis module for cleaned articles.

Covers:
  - FinBERT-based sentiment classification (crypto/finance-aware)
  - VADER fallback (rule-based, no model loading)
  - Text building from article fields (title + summary + content excerpt)
  - Per-article and global aggregation of sentiment scores
  - Pluggable engine interface via Protocol

Key improvements over original:
  - FinBERT provides domain-aware sentiment for crypto/finance text
    where VADER's general English lexicon struggles with terms like
    "moon", "dumped", "FUD", "HODL" which have different polarity
    depending on context.
  - Pluggable engine via Protocol — swap engines without touching callers.
  - Per-engine caching of large models to avoid repeated loading.
"""

from __future__ import annotations

import dataclasses
import logging
import threading
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


@dataclasses.dataclass(slots=True)
class CompareResult:
    """Side-by-side sentiment comparison between two engines."""

    # VADER fields
    vader_label: str
    vader_compound: float
    vader_pos: float
    vader_neu: float
    vader_neg: float

    # FinBERT fields
    finbert_label: str | None = None
    finbert_compound: float | None = None
    finbert_pos: float | None = None
    finbert_neu: float | None = None
    finbert_neg: float | None = None

    # Composite text used
    composite_text: str = ""


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
    """Build a composite text from article fields for sentiment analysis.

    Priority order:
      1. Title (headline carries strong sentiment signals)
      2. Summary
      3. Short excerpt of content (first ~300 chars)

    Punctuation and capitalisation are intentionally preserved — both VADER and
    transformer models use them as key signals (!, ALL CAPS).

    Args:
        title: Article headline.
        summary: Article summary/description.
        content: Full article body (only first 300 chars used).
        max_len: Hard cap on total composite text length.

    Returns:
        The composite text ready for sentiment analysis.
    """
    parts: list[str] = []

    if title:
        parts.append(title)

    if summary:
        parts.append(summary)

    # Content excerpt — keep it short to avoid diluting headline sentiment.
    if content:
        excerpt = content.strip()[:300]
        if excerpt:
            parts.append(excerpt)

    composite = " ".join(parts)

    # Hard cap on total length.
    if len(composite) > max_len:
        composite = composite[:max_len]

    return composite


# ------------------------------------------------------------------ FinBERT engine ---


class FinBERTSentimentEngine:
    """FinBERT-based sentiment analysis engine.

    Uses the HuggingFace ``ProsusAI/finbert`` model — a BERT transformer
    fine-tuned on financial text for 3-class classification (positive, neutral, negative).

    This is specifically trained on financial headlines and news articles,
    making it significantly more accurate than VADER for crypto/finance text.

    Requirements:
        pip install torch transformers sentencepiece

    The model (~400 MB) is downloaded and cached on first use, then re-used
    via module-level singleton with thread-safe locking.
    """

    MODEL_NAME = "ProsusAI/finbert"
    LABEL_POSITIVE = "positive"
    LABEL_NEUTRAL = "neutral"
    LABEL_NEGATIVE = "negative"

    THRESHOLD_POS = 0.5
    THRESHOLD_NEG = 0.5

    engine_name = "finbert"

    _lock = threading.Lock()
    _pipeline = None

    @classmethod
    def _get_pipeline(cls):
        """Return a cached pipeline, loading the model on first use."""
        if cls._pipeline is None:
            with cls._lock:
                # Double-check after acquiring lock.
                if cls._pipeline is None:
                    try:
                        from transformers import (  # noqa: PLC0415
                            pipeline,
                            AutoTokenizer,
                            AutoModelForSequenceClassification,
                        )

                        tokenizer = AutoTokenizer.from_pretrained(cls.MODEL_NAME)
                        model = AutoModelForSequenceClassification.from_pretrained(
                            cls.MODEL_NAME,
                            local_files_only=False,
                            trust_remote_code=False,
                        )

                        cls._pipeline = pipeline(
                            "text-classification",
                            model=model,
                            tokenizer=tokenizer,
                            top_k=None,  # return all scores (replaces deprecated return_all_scores)
                            device=0,  # GPU if available; falls back to CPU
                        )
                        logger.info("FinBERT model loaded from %s", cls.MODEL_NAME)
                    except Exception:
                        # Mark as failed — caller will get None.
                        cls._pipeline = None
                        logger.warning(
                            "Failed to load FinBERT model — fall back to VADER. "
                            "Install: pip install torch transformers sentencepiece",
                            exc_info=True,
                        )
        return cls._pipeline

    def analyze(self, text: str) -> SentimentResult:
        """Analyse *text* using the FinBERT pipeline.

        Args:
            text: Article composite text (title + summary + content excerpt).

        Returns:
            A ``SentimentResult`` with per-class probabilities and compound score.
        """
        pipe = self._get_pipeline()
        if pipe is None or pipe == "load_failed":
            # Fallback to a neutral result — the scheduler will try VADER as secondary.
            return SentimentResult(
                sentiment_neg=0.0,
                sentiment_neu=1.0,
                sentiment_pos=0.0,
                sentiment_compound=0.0,
                sentiment_label=self.LABEL_NEUTRAL,
                sentiment_engine=self.engine_name,
            )

        try:
            # FinBERT expects relatively short inputs — cap at 512 tokens.
            truncated = text[:512]
            outputs = pipe(truncated, truncation=True, max_length=512)

            # outputs is a list like [[{"label": "positive", "score": 0.8}, ...], ...]
            if outputs and isinstance(outputs[0], list):
                scores_map = {item["label"]: item["score"] for item in outputs[0]}

                pos_score = scores_map.get(self.LABEL_POSITIVE, 0.0)
                neg_score = scores_map.get(self.LABEL_NEGATIVE, 0.0)
                neu_score = scores_map.get(self.LABEL_NEUTRAL, 0.0)

                # Compound score: positive - negative (maps to VADER's [-1, 1] range)
                compound = pos_score - neg_score

                label = self.classify(compound)

                return SentimentResult(
                    sentiment_neg=neg_score,
                    sentiment_neu=neu_score,
                    sentiment_pos=pos_score,
                    sentiment_compound=round(compound, 6),
                    sentiment_label=label,
                    sentiment_engine=self.engine_name,
                )

            # Unexpected output format — return neutral.
            logger.warning("Unexpected FinBERT output format: %s", outputs)
            return SentimentResult(
                sentiment_neg=0.0,
                sentiment_neu=1.0,
                sentiment_pos=0.0,
                sentiment_compound=0.0,
                sentiment_label=self.LABEL_NEUTRAL,
                sentiment_engine=self.engine_name,
            )

        except Exception as exc:
            logger.warning("FinBERT analysis error: %s", exc)
            return SentimentResult(
                sentiment_neg=0.0,
                sentiment_neu=1.0,
                sentiment_pos=0.0,
                sentiment_compound=0.0,
                sentiment_label=self.LABEL_NEUTRAL,
                sentiment_engine=self.engine_name,
            )

    def classify(self, compound: float) -> str:
        """Classify a single compound score into a label."""
        if compound >= 0.1:
            return self.LABEL_POSITIVE
        if compound <= -0.1:
            return self.LABEL_NEGATIVE
        return self.LABEL_NEUTRAL


# ------------------------------------------------------------------ VADER engine (fallback) ---


class VaderSentimentEngine:
    """VADER-based sentiment analysis engine.

    Uses the compound score with standard thresholds:
      - compound >= 0.05 -> positive
      - compound <= -0.05 -> negative
      - otherwise         -> neutral

    Included as a fallback when FinBERT is not available or fails to load.
    """

    LABEL_POSITIVE = "positive"
    LABEL_NEUTRAL = "neutral"
    LABEL_NEGATIVE = "negative"

    THRESHOLD_POS = 0.05
    THRESHOLD_NEG = -0.05

    engine_name = "vader"

    _analyzer = None
    _lock = threading.Lock()

    @classmethod
    def _get_analyzer(cls):
        """Return a cached SentimentIntensityAnalyzer."""
        if cls._analyzer is None:
            with cls._lock:
                if cls._analyzer is None:
                    from vaderSentiment.vaderSentiment import (  # noqa: PLC0415
                        SentimentIntensityAnalyzer,
                    )

                    cls._analyzer = SentimentIntensityAnalyzer()
        return cls._analyzer

    def analyze(self, text: str) -> SentimentResult:
        """Analyse *text* and return a ``SentimentResult``."""
        scores = self._get_analyzer().polarity_scores(text)
        label = self.classify(scores["compound"])

        return SentimentResult(
            sentiment_neg=scores["neg"],
            sentiment_neu=scores["neu"],
            sentiment_pos=scores["pos"],
            sentiment_compound=round(scores["compound"], 6),
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


# ------------------------------------------------------------------ Ensemble engine ---


class EnsembleSentimentEngine:
    """Ensemble of FinBERT + VADER for more robust sentiment.

    When both engines are available, the ensemble combines their compound scores
    with weighted averaging (FinBERT gets 70% weight as it is domain-aware).

    If FinBERT fails to load, falls back to pure VADER.
    """

    FINBERT_WEIGHT = 0.7
    VADER_WEIGHT = 0.3

    engine_name = "ensemble"

    def __init__(self):
        self._finbert = FinBERTSentimentEngine()
        self._vader = VaderSentimentEngine()
        self._using_fallback = False

    def analyze(self, text: str) -> SentimentResult:
        """Analyse using ensemble (FinBERT + VADER), falling back to VADER alone."""
        finbert_result = self._finbert.analyze(text)

        # If FinBERT returned neutral with zero scores, it likely failed to load.
        if (
            finbert_result.sentiment_compound == 0.0
            and finbert_result.sentiment_neu == 1.0
        ):
            logger.debug("FinBERT unavailable — using VADER-only engine")
            self._using_fallback = True
            return self._vader.analyze(text)

        vader_result = self._vader.analyze(text)

        # Weighted average compound score
        compound = (
            self.FINBERT_WEIGHT * finbert_result.sentiment_compound
            + self.VADER_WEIGHT * vader_result.sentiment_compound
        )
        label = self.classify(compound)

        return SentimentResult(
            sentiment_neg=round(
                self.FINBERT_WEIGHT * finbert_result.sentiment_neg
                + self.VADER_WEIGHT * vader_result.sentiment_neg,
                6,
            ),
            sentiment_neu=round(
                self.FINBERT_WEIGHT * finbert_result.sentiment_neu
                + self.VADER_WEIGHT * vader_result.sentiment_neu,
                6,
            ),
            sentiment_pos=round(
                self.FINBERT_WEIGHT * finbert_result.sentiment_pos
                + self.VADER_WEIGHT * vader_result.sentiment_pos,
                6,
            ),
            sentiment_compound=round(compound, 6),
            sentiment_label=label,
            sentiment_engine=self.engine_name,
        )

    def classify(self, compound: float) -> str:
        """Classify a single compound score into a label."""
        if compound >= 0.1:
            return "positive"
        if compound <= -0.1:
            return "negative"
        return "neutral"


# ------------------------------------------------------------------ Compare engine ---


def compare_sentiment(
    title: str,
    summary: str = "",
    content: str = "",
) -> CompareResult:
    """Run both VADER and FinBERT on the same text for comparison.

    Useful for debugging, testing model accuracy, or showing users
    how each engine interprets the same headline differently.

    Args:
        title: Article headline (sentiment-rich).
        summary: Article summary/description.
        content: Full article body (excerpt used).

    Returns:
        ``CompareResult`` with both engines' outputs side by side.
    """
    # Build composite text (same logic as analyze_sentiment)
    composite = build_sentiment_text(
        title=title,
        summary=summary,
        content=content,
    )

    # Run VADER
    vader_engine = VaderSentimentEngine()
    vader_result = vader_engine.analyze(composite)

    # Run FinBERT (if available)
    finbert_engine = FinBERTSentimentEngine()
    finbert_result = finbert_engine.analyze(composite)

    return CompareResult(
        vader_label=vader_result.sentiment_label,
        vader_compound=vader_result.sentiment_compound,
        vader_pos=vader_result.sentiment_pos,
        vader_neu=vader_result.sentiment_neu,
        vader_neg=vader_result.sentiment_neg,
        finbert_label=finbert_result.sentiment_label,
        finbert_compound=finbert_result.sentiment_compound,
        finbert_pos=finbert_result.sentiment_pos,
        finbert_neu=finbert_result.sentiment_neu,
        finbert_neg=finbert_result.sentiment_neg,
        composite_text=composite,
    )


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
        # Default: ensemble with FinBERT + VADER fallback to pure VADER.
        engine = EnsembleSentimentEngine()

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
            sentiment_label="neutral",
            sentiment_engine=engine.engine_name if hasattr(engine, "engine_name") else "",
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
        "positive": 0,
        "neutral": 0,
        "negative": 0,
    }
    for r in results:
        label = r.sentiment_label
        counts[label] = counts.get(label, 0) + 1

    # Global label = the most common class.
    global_label = max(counts, key=lambda k: counts[k])  # type: ignore[arg-type]

    return {
        "mean_compound": round(mean_compound, 6),
        "global_label": global_label,
        "count_positive": counts.get("positive", 0),
        "count_neutral": counts.get("neutral", 0),
        "count_negative": counts.get("negative", 0),
        "total": total,
    }
