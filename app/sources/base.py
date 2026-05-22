"""Shared types, HTTP helpers, and RSS parser for all source adapters."""

from __future__ import annotations

import dataclasses
import logging
import re
import time
from datetime import datetime, timezone
from html import unescape
from typing import Any

import feedparser
import httpx

logger = logging.getLogger(__name__)

# ---------- Constants ----------

_DEFAULT_TIMEOUT = 15.0
_CUSTOM_USER_AGENT = (
    "CryptoNewsSentimentMonitor/1.0 (+https://github.com/ragostino74/crypto-news-sentiment-monitor)"
)

_http_client: httpx.Client | None = None

# ---------- Data model ----------


@dataclasses.dataclass(slots=True, frozen=False)
class RawArticle:
    """Normalised article coming from any source adapter."""

    source: str  # human-readable name, e.g. "CoinDesk"
    title: str  # headline / title
    url: str  # canonical article URL
    published_at: datetime | None  # parsed publish date (UTC), may be None
    summary: str  # short description / snippet
    content: str  # full HTML body (may be long)

    def __repr__(self) -> str:
        return f"<RawArticle source={self.source!r} title={self.title[:50]!r}>"


# ---------- HTTP helper ----------


def _get_http_client() -> httpx.Client:
    global _http_client
    if _http_client is None:
        _http_client = httpx.Client(
            timeout=httpx.Timeout(_DEFAULT_TIMEOUT),
            headers={"User-Agent": _CUSTOM_USER_AGENT},
        )
    return _http_client


def fetch_http(url: str, timeout: float | None = None) -> httpx.Response:
    """Perform a GET request with custom user-agent and timeout."""
    client = _get_http_client()
    return client.get(url, timeout=timeout or _DEFAULT_TIMEOUT)


# ---------- RSS helper ----------


def fetch_rss_feed(feed_url: str) -> feedparser.FeedParserDict:
    """Fetch and parse an RSS/Atom feed. Returns a feedparser Feed object."""
    resp = fetch_http(feed_url)
    if resp.status_code != 200:
        logger.warning("RSS %s returned HTTP %d", feed_url, resp.status_code)
        return feedparser.parse(b"")
    return feedparser.parse(resp.content)


# ---------- HTML helpers ----------

_HTML_RE = re.compile(r"<[^>]+>")


def strip_html_tags(html: str | None) -> str:
    """Remove all HTML tags from a string."""
    if not html:
        return ""
    return _HTML_RE.sub("", html).strip()


def normalize_summary(entry: dict[str, Any]) -> str:
    """Extract a plain-text summary from a feed entry.

    Tries (in order):
      1. ``summary`` — first-choice snippet (may be HTML)
      2. ``content_detail`` — full content stripped of HTML
      3. ``content`` — fallback stripped of HTML
      4. Empty string
    """
    # Direct text summary
    val = entry.get("summary")
    if val and isinstance(val, str):
        return strip_html_tags(val)

    # content_detail or content may be a list of dicts (feedparser standard)
    for key in ("content_detail", "content"):
        raw = entry.get(key)
        if isinstance(raw, list) and raw:
            first = raw[0]
            if isinstance(first, dict):
                html_val = first.get("value") or ""
            else:
                html_val = str(first)
            return strip_html_tags(html_val)

    return ""


# ---------- Date parsing ----------


def _parse_feed_date(entry: dict[str, Any]) -> datetime | None:
    """Extract a UTC datetime from a feed entry's date fields.

    Checks (in order):
      1. published_parsed — time.struct_time
      2. updated_parsed — time.struct_time
      3. published — ISO / RFC-2822 string
      4. updated — same
    """
    # Direct struct_time
    for attr in ("published_parsed", "updated_parsed"):
        parsed = entry.get(attr)
        if isinstance(parsed, time.struct_time):
            try:
                return datetime(*parsed[:6], tzinfo=timezone.utc)
            except (ValueError, OverflowError):
                continue

    # String-based date fields
    for attr in ("published", "updated"):
        val = entry.get(attr)
        if not val or not isinstance(val, str):
            continue

        for fmt in (
            "%a, %d %b %Y %H:%M:%S %z",  # RFC-2822
            "%Y-%m-%dT%H:%M:%S%z",       # ISO with tz
            "%Y-%m-%dT%H:%M:%SZ",        # ISO UTC
            "%Y-%m-%d %H:%M:%S",         # naive datetime
        ):
            try:
                return datetime.strptime(val, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue

    return None


# ---------- Entry helpers ----------


def _extract_link(entry: dict[str, Any]) -> str | None:
    """Extract the canonical URL from a feed entry."""
    links = entry.get("links")
    if not isinstance(links, list):
        return None

    # Prefer 'alternate' link type
    for link in links:
        if isinstance(link, dict) and link.get("rel") == "alternate":
            return link.get("href", "")
    # Fallback to first available link
    first = links[0]
    if isinstance(first, dict):
        return first.get("href", "")
    if isinstance(first, str):
        return first
    return None


def entry_to_article(source_name: str, entry: dict[str, Any]) -> RawArticle | None:
    """Convert a single feedparser entry into a RawArticle.

    Returns None when required fields (title, url) are missing.
    """
    # Title — try title_detail value first (feedparser standard), then raw title
    title_detail = entry.get("title_detail")
    if isinstance(title_detail, dict) and title_detail.get("value"):
        title = title_detail["value"]
    else:
        title = str(entry.get("title", ""))

    url = _extract_link(entry)
    if not title or not url:
        logger.debug(
            "Skipping malformed entry: title=%r url=%r", title, url
        )
        return None

    published_at = _parse_feed_date(entry)
    summary = normalize_summary(entry)

    # For content, try content_detail first, then content (list of dicts)
    raw_content: str = ""
    for key in ("content_detail", "content"):
        val = entry.get(key)
        if isinstance(val, list) and val:
            first = val[0]
            if isinstance(first, dict):
                raw_content = first.get("value") or ""
                break
            raw_content = str(first)
            break
        elif isinstance(val, str) and val:
            raw_content = val
            break

    content = strip_html_tags(raw_content)[:2000]

    return RawArticle(
        source=source_name,
        title=unescape(title.strip()),
        url=url.strip(),
        published_at=published_at,
        summary=summary[:500],
        content=content,
    )
