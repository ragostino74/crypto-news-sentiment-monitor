"""Services layer: normalization, deduplication, and processing pipelines."""

from .dedupe import clean_and_dedupe, normalize_article
from .normalization import (
    canonicalize_url,
    normalize_whitespace,
    normalize_text_field,
)
from .sentiment import (
    SentimentEngine,
    SentimentResult,
    VaderSentimentEngine,
    build_sentiment_text,
    analyze_sentiment,
    classify_vader,
    aggregate_sentiment,
)

__all__ = [
    # dedupe / normalization
    "clean_and_dedupe",
    "normalize_article",
    "canonicalize_url",
    "normalize_whitespace",
    "normalize_text_field",
    # sentiment
    "SentimentResult",
    "SentimentEngine",
    "VaderSentimentEngine",
    "build_sentiment_text",
    "analyze_sentiment",
    "classify_vader",
    "aggregate_sentiment",
]
