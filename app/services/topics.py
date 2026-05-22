"""Crypto topic detection for article articles.

Maps detected cryptocurrency tokens (from title + summary) to a canonical
list of the 20 most-followed cryptos. Used by the scheduler at ingest time
and by the API/dashboard for filtering and per-crypto sentiment views.

Detection strategy: word-boundary regex matching against each crypto's
canonical symbol and full name. Returns matches in order of first appearance
in the text (not list order).

Short symbols (<= 4 chars) are excluded from regex detection to avoid false
positives with common English words (e.g. "link", "ton", "dot"). Those cryptos
are detected only by their full name / long aliases.
"""

from __future__ import annotations

import re
from typing import List, Tuple


# ------------------------------------------------------------------ Canonical list ---
# Each entry: (display_name, symbol/ticker, [aliases used for detection])

CRYPTO_LIST: list[tuple[str, str, list[str]]] = [
    ("Bitcoin", "BTC", ["bitcoin"]),
    ("Ethereum", "ETH", ["ethereum"]),
    ("Tether", "USDT", ["tether"]),
    ("BNB", "BNB", ["bnb", "binance coin"]),
    ("XRP", "XRP", ["xrp", "ripple"]),
    ("Solana", "SOL", ["solana", "sol"]),
    ("USDC", "USDC", ["usdc", "usd coin"]),
    ("Cardano", "ADA", ["cardano", "ada"]),
    ("Dogecoin", "DOGE", ["dogecoin", "doge"]),
    ("Toncoin", "TON", ["toncoin", "ton", "the open network"]),
    ("Avalanche", "AVAX", ["avalanche", "avax"]),
    ("Shiba Inu", "SHIB", ["shiba inu", "shib"]),
    ("Polkadot", "DOT", ["polkadot", "dot"]),
    ("Chainlink", "LINK", ["chainlink"]),
    ("Polygon", "MATIC", ["polygon", "matic", "poly"]),
    ("Litecoin", "LTC", ["litecoin", "ltc"]),
    ("Dai", "DAI", ["dai", "makerdao"]),
    ("Tron", "TRX", ["tron", "trx"]),
    ("Uniswap", "UNI", ["uniswap", "uni"]),
    ("Cosmos", "ATOM", ["cosmos", "atom"]),
]

# Symbols short enough to be common English words — skip regex for these.
_SHORT_SYMBOLS: set[str] = {"sol", "ton", "dot", "dai", "uni", "link"}


# ------------------------------------------------------------------ Index building ---

def _find_all_matches(text_lower: str) -> List[Tuple[int, int, str]]:
    """Find all crypto matches in *text_lower*.

    Returns a list of (start_pos, end_pos, display_name) sorted by the
    position where each match first appears in the text.
    """
    matches: List[Tuple[int, int, str]] = []

    for i, (display, token, aliases) in enumerate(CRYPTO_LIST):
        # --- Symbol pattern (word boundary) ---
        # Skip short symbols that are common English words.
        if token.lower() not in _SHORT_SYMBOLS:
            pat = re.compile(r"\b" + re.escape(token.lower()) + r"\b")
            for m in pat.finditer(text_lower):
                matches.append((m.start(), m.end(), display))

        # --- Alias patterns (word boundary) ---
        for alias in aliases:
            pat = re.compile(r"\b" + re.escape(alias.lower()) + r"\b")
            for m in pat.finditer(text_lower):
                matches.append((m.start(), m.end(), display))

    # Sort by start position → text order
    matches.sort(key=lambda x: x[0])
    return matches


def detect_topics_v2(text: str | None) -> list[str]:
    """Detect crypto topics in text.

    Scans the text for each crypto's canonical symbol and full name / alias
    using word-boundary regex. Returns a deduplicated list of display names
    preserving first-match order (order they appear in text).

    Short symbols (<= 4 chars like SOL, TON, DOT, DAI, UNI) are excluded
    from regex matching to avoid false positives with common English words.

    Args:
        text: Title + summary combined (or None).

    Returns:
        List of crypto display names found, or empty list.
    """
    if not text:
        return []

    text_lower = text.lower()
    matches = _find_all_matches(text_lower)

    # Deduplicate by display name, preserving text-order
    seen: set[str] = set()
    result: list[str] = []
    for _, _, display in matches:
        if display not in seen:
            seen.add(display)
            result.append(display)

    return result


def get_crypto_list() -> list[tuple[str, str]]:
    """Return the canonical crypto list as (display_name, symbol) tuples."""
    return [(display, token) for display, token, _ in CRYPTO_LIST]


# Backwards compat alias.
detect_cryptos = detect_topics_v2
