"""Text normalization helpers for RawArticle fields.

Covers:
  - Whitespace collapsing and stripping
  - URL canonicalization (scheme, trailing slashes, query-param ordering)
  - Unicode normalisation (NFC)
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

# Single regex for collapsing any run of whitespace (spaces, tabs, newlines)
# into a single ASCII space.
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_whitespace(text: str | None) -> str:
    """Collapse all whitespace runs to a single space and strip."""
    if not text:
        return ""
    # Normalize unicode whitespace (non-breaking space, etc.) first
    text = text.replace("\u00a0", " ").replace("\u200b", "")
    return _WHITESPACE_RE.sub(" ", text).strip()


def normalize_text_field(text: str | None) -> str:
    """Normalize a text field for display and comparison.

    Steps:
      1. Unicode NFC normalisation
      2. Whitespace collapsing via normalize_whitespace
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    return normalize_whitespace(text)


# ------------------------------------------------------------------ URL canonicalization ---

# Domains that always serve content over HTTPS; force scheme.
_FORCE_HTTPS = {
    "coindesk.com",
    "cointelegraph.com",
    "decrypt.co",
    "cryptoslate.com",
    "theblock.co",
}

# Trailing-path segments we strip (feed/article slug patterns).
_TRAILING_SLASH_RE = re.compile(r"/index\.html?$")


def canonicalize_url(url: str | None) -> str:
    """Return a canonical form of *url* suitable for deduplication.

    Operations (in order):
      1. Normalise whitespace and lowercase the host.
      2. Force HTTPS for known crypto domains.
      3. Remove ``index.html`` / ``index.htm`` from path.
      4. Ensure exactly one trailing slash only for ``/`` root.
      5. Sort query parameters alphabetically by key, drop empty values.
      6. Remove ``utm_`` tracking params entirely.
    """
    if not url:
        return ""

    url = normalize_whitespace(url).lower()
    parsed = urlparse(url)

    # --- scheme -----------------------------------------------------------------
    netloc = parsed.netloc.lower()
    force_https = any(netloc.endswith("." + d) or netloc == d for d in _FORCE_HTTPS)
    if force_https:
        parsed = parsed._replace(scheme="https")

    # Remove default ports
    scheme = parsed.scheme.lower()
    port = parsed.port
    if port and ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = parsed.hostname or ""
    else:
        netloc = parsed.netloc

    # --- path -------------------------------------------------------------------
    path = parsed.path
    path = _TRAILING_SLASH_RE.sub("", path)
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    # --- query (sorted, deduplicated, utm removed, empty values dropped) -------
    params = parse_qs(parsed.query, keep_blank_values=True)
    sorted_params: list[tuple[str, str]] = []
    for key in sorted(params):
        if key.startswith("utm_"):
            continue
        for val in sorted(set(params[key])):
            if val:  # drop empty values
                sorted_params.append((key, val))

    query = urlencode(sorted_params)

    return urlunparse((parsed.scheme, netloc, path, parsed.params, query, parsed.fragment))
