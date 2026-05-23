"""Services layer: normalization, deduplication, sentiment analysis, and scheduling."""

from .dedupe import clean_and_dedupe, normalize_article
from .normalization import (
    canonicalize_url,
    normalize_whitespace,
    normalize_text_field,
)
from .scheduler import (
    RunResult,
    Scheduler,
    get_scheduler,
    run_pipeline,
)
from .sentiment import (
    SentimentEngine,
    SentimentResult,
    VaderSentimentEngine,
    FinBERTSentimentEngine,
    EnsembleSentimentEngine,
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
    # scheduler
    "RunResult",
    "Scheduler",
    "get_scheduler",
    "run_pipeline",
    # sentiment
    "SentimentResult",
    "SentimentEngine",
    "VaderSentimentEngine",
    "FinBERTSentimentEngine",
    "EnsembleSentimentEngine",
    "build_sentiment_text",
    "analyze_sentiment",
    "classify_vader",
    "aggregate_sentiment",
]
