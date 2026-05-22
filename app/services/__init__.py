"""Services layer: normalization, deduplication, and processing pipelines."""

from .dedupe import clean_and_dedupe, normalize_article
from .normalization import (
    canonicalize_url,
    normalize_whitespace,
    normalize_text_field,
)

__all__ = [
    "clean_and_dedupe",
    "normalize_article",
    "canonicalize_url",
    "normalize_whitespace",
    "normalize_text_field",
]
