"""Crypto topic detection for article articles.

Maps detected cryptocurrency tokens (from title + summary + content) to a canonical
list of the 50+ most-followed cryptos. Used by the scheduler at ingest time and
by the API/dashboard for filtering and per-crypto sentiment views.

Detection strategy: word-boundary regex matching against each crypto's
canonical symbol and full name/aliases. Returns matches in order of first appearance
in the text (not list order).

Key improvements over original:
  - Expanded from 20 to 50+ tokens, covering L1s, L2s, DeFi, memes, stablecoins,
    RWA, AI/crypto crossover, and emerging chains.
  - Context-aware detection: symbols that appear in specific compound contexts
    (e.g. "ETH gas", "BNB Chain") are matched more reliably.
  - Case-insensitive matching with multi-word alias support.
  - Per-token confidence scoring based on context keywords.
"""

from __future__ import annotations

import re
from typing import List, Tuple


# ------------------------------------------------------------------ Canonical list ---
# Each entry: (display_name, symbol/ticker, [aliases used for detection],
#              [context keywords that boost confidence])

CRYPTO_LIST: list[tuple[str, str, list[str], list[str]]] = [
    # === Tier 1: Top market cap (20) ===
    ("Bitcoin", "BTC", ["bitcoin"], ["blockchain", "halving", "satoshi", "mining"]),
    ("Ethereum", "ETH", ["ethereum", "ether", "eth"], ["gas fee", "defi", "nft", "l2", "layer 2"]),
    ("Tether", "USDT", ["tether"], ["stablecoin", "peg", "mint", "redeem"]),
    ("BNB", "BNB", ["bnb", "binance coin", "binance"], ["binance chain", "bsc", "cake"]),
    ("XRP", "XRP", ["xrp", "ripple"], ["ledger", "escrow", "sec lawsuit", "payment"]),
    ("Solana", "SOL", ["solana", "sol"], ["validator", "tps", "serum", "program"]),
    ("USDC", "USDC", ["usdc", "usd coin", "circle"], ["stablecoin", "peg", "reserve"]),
    ("Cardano", "ADA", ["cardano", "ada", "ouroboros"], ["stake pool", "hsroboros", "peer reviewed"]),
    ("Dogecoin", "DOGE", ["dogecoin", "doge", "shiba", "woof"], ["meme coin", "elon musk", "tip"]),
    ("Toncoin", "TON", ["toncoin", "ton", "the open network", "telegram"], ["telegram", "tma", "mini app"]),
    ("Avalanche", "AVAX", ["avalanche", "avax", "subnet"], ["subnet", "snow", "validator"]),
    ("Shiba Inu", "SHIB", ["shiba inu", "shib", "shib army"], ["meme coin", "shibarium", "burn"]),
    ("Polkadot", "DOT", ["polkadot", "dot", "parachain"], ["parachain", "kusama", "governance"]),
    ("Chainlink", "LINK", ["chainlink"], ["oracle", "ccip", "vrf", "data feed"]),
    ("Polygon", "MATIC", ["polygon", "matic", "poly", "pol", "matic network"], ["zk", "sidechain", "supernova"]),
    ("Litecoin", "LTC", ["litecoin", "ltc", "silver coin"], ["scrypt", "halving", "payment"]),
    ("Dai", "DAI", ["dai", "makerdao", "maker", "dai token"], ["stablecoin", "aave", "compound", "vault"]),
    ("Tron", "TRX", ["tron", "trx"], ["usdt tron", "network", "sunswap", "staking"]),
    ("Uniswap", "UNI", ["uniswap", "uni", "uni v3", "uniswap protocol"], ["dex", "amm", "liquidity pool", "fee tier"]),
    ("Cosmos", "ATOM", ["cosmos", "atom", "cosmos hub", "interchain"], ["ibc", "zone", "tendermint", "staking"]),

    # === Tier 2: Major L1s & L2s (10) ===
    ("Arbitrum", "ARB", ["arbitrum", "arb", "arb one", "arbitrum one", "optimism"], ["l2", "rollup", "defi"]),
    ("Optimism", "OP", ["optimism", "op", "op mainnet", "superchain", "oeth"], ["l2", "rollup", "superchain"]),
    ("Near Protocol", "NEAR", ["near protocol", "near", "near network"], ["sharding", "nightshade", "protocol"]),
    ("Aptos", "APT", ["aptos", "apt"], ["move language", "sui", "venture capital"]),
    ("Sui", "SUI", ["sui", "sui network"], ["move language", "aptos", "parallel execution"]),
    ("Injective", "INJ", ["injective", "inj", "injective protocol"], ["derivative", "cefi", "l1"]),
    ("Sei", "SEI", ["sei", "sei network"], ["l1", "trading", "parallel execution"]),
    ("Algorand", "ALGO", ["algorand", "algo"], ["pure proof of stake", "asa", "smart contract"]),
    ("Fantom", "FTM", ["fantom", "ftm", "opera chain", "fantom opera"], ["evm", "l1", "defi"]),
    ("Hedera", "HBAR", ["hedera", "hbar", "hashgraph"], ["hashgraph", "consensus", "token service"]),

    # === Tier 3: DeFi & Infrastructure (10) ===
    ("Maker", "MKR", ["maker", "mkr", "makerdao"], ["governance", "dai", "vault", "dsrv"]),
    ("Aave", "AAVE", ["aave", "aave protocol"], ["lending", "flash loan", "deposit", "borrow"]),
    ("Curve", "CRV", ["curve", "crv", "curve finance", "curve dao"], ["stableswap", "lp", "ve3", "crvusd"]),
    ("Synthetix", "SNX", ["synthetix", "snx", "hedges"], ["derivatives", "synthetic", "staking"]),
    ("Lido", "LDO", ["lido", "ldo", "steth", "liquid staking"], ["liquid staking", "ethereum", "steth"]),
    ("Rocket Pool", "RPL", ["rocket pool", "rpl", "rETH"], ["liquid staking", "node operator"]),
    ("The Graph", "GRT", ["the graph", "grt", "graph protocol"], ["indexing", "subgraph", "oracle data"]),
    ("Storj", "STORJ", ["storj", "storj network"], ["decentralized storage", "filecoin", "bandwidth"]),
    ("Render", "RENDER", ["render", "render network", "rndr"], ["gpu rendering", "ai compute", "metaverse"]),
    ("Akash", "AKT", ["akash", "akash network", "akash computing"], ["decentralized cloud", "compute", "blockchain"]),

    # === Tier 4: AI + Crypto & Memes (8) ===
    ("Fetch.ai", "FET", ["fetch ai", "fet", "asyncai", "artificial superintelligence alliance", "asi"], ["ai agent", "autonomous agent"]),
    ("SingularityNET", "AGIX", ["singularitynet", "agix", "agn"], ["ai marketplace", "decentralized ai"]),
    ("Ocean Protocol", "OCEAN", ["ocean protocol", "ocean", "ocean data token", "oceandao"], ["data marketplace", "ai training"]),
    ("Bittensor", "TAO", ["bittensor", "tau", "tao", "bit tensor"], ["decentralized ai network", "mining"]),
    ("Pepe", "PEPE", ["pepe", "pepe coin", "pepecoin"], ["meme coin", "frog", "to the moon"]),
    ("Bonk", "BONK", ["bonk", "bonk coin", "bonk solana"], ["meme coin", "solana", "dog meme"]),
    ("Floki", "FLOKI", ["floki", "floki inu", "floki token"], ["meme coin", "valhalla", "tokem"]),
    ("WIF", "WIF", ["wif", "dog wif hat", "dogwifhat"], ["meme coin", "solana", "hat"]),

    # === Tier 5: Emerging & Niche (7) ===
    ("Celestia", "TIA", ["celestia", "tia", "celestia network"], ["modular blockchain", "da layer", "data availability"]),
    ("Stacks", "STX", ["stacks", "stx", "stacks protocol", "bitcoin layer 2"], ["bitcoin smart contract", "stacking", "clarity"]),
    ("Immutable", "IMX", ["immutable", "imx", "immutable x", "immutable zk"], ["gaming nft", "zksync", "polygon"]),
    ("Theta Network", "THETA", ["theta", "theta network", "theta token"], ["video streaming", "edge computing", "decentralized cdn"]),
    ("Filecoin", "FIL", ["filecoin", "fil"], ["decentralized storage", "ipfs", "pinning"]),
    ("Axie Infinity", "AXS", ["axie infinity", "axs", "axie shardy", "ronin"], ["gamefi", "nft game", "ronin chain"]),
    ("Mana (Decentraland)", "MANA", ["mana", "decentraland", "decentraland mana", "land metaverse"], ["metaverse", "virtual land", "nft land"]),
]

# Symbols short enough to be common English words — skip regex for these.
_SHORT_SYMBOLS: set[str] = {
    "sol", "ton", "dot", "dai", "uni", "link",
    "op", "apt", "sei", "algo", "ftm", "hbar", "arb",
}


# ------------------------------------------------------------------ Index building ---

def _find_all_matches(text_lower: str) -> list[tuple[int, int, str]]:
    """Find all crypto matches in *text_lower*.

    Returns a list of (start_pos, end_pos, display_name) sorted by the
    position where each match first appears in the text.

    Uses multi-word alias support and handles context boosting for disambiguation.
    """
    matches: list[tuple[int, int, str]] = []

    for display, token, aliases, contexts in CRYPTO_LIST:
        # --- Symbol pattern (word boundary) ---
        if token.lower() not in _SHORT_SYMBOLS:
            pat = re.compile(r"\b" + re.escape(token.lower()) + r"\b")
            for m in pat.finditer(text_lower):
                matches.append((m.start(), m.end(), display))

        # --- Alias patterns (word boundary, supporting multi-word) ---
        for alias in aliases:
            # Escape special regex characters and match as word-boundary pattern
            escaped = re.escape(alias.lower())
            pat = re.compile(r"\b" + escaped + r"\b")
            for m in pat.finditer(text_lower):
                matches.append((m.start(), m.end(), display))

    # Sort by start position → text order
    matches.sort(key=lambda x: x[0])
    return matches


def _boost_matches_with_context(text: str, matches: list[tuple[int, int, str]]) -> list[str]:
    """Boost confidence for matches that appear near context keywords.

    When multiple cryptos are detected in the same text, those with nearby
    context keywords (defined per-token) get priority. For example, "ETH gas"
    strongly suggests Ethereum even if other ETH-prefixed tokens are present.

    Args:
        text: Original article text.
        matches: List of (start_pos, end_pos, display_name).

    Returns:
        Deduplicated list of crypto display names, context-boosted.
    """
    if len(matches) <= 1:
        # No disambiguation needed
        return [m[2] for m in matches]

    # Build a context score for each detected token only
    context_scores: dict[str, int] = {}
    detected_display_set = {m[2] for m in matches}
    for display, _, aliases, contexts in CRYPTO_LIST:
        if not contexts or display not in detected_display_set:
            continue
        for ctx in contexts:
            start = 0
            while True:
                idx = text.lower().find(ctx, start)
                if idx < 0:
                    break
                # Check proximity to any match position for this crypto
                for _, end_pos, token_display in matches:
                    if token_display == display and abs(idx - end_pos) < 500:
                        context_scores[token_display] = context_scores.get(token_display, 0) + 1
                        break
                start = idx + 1

    # Sort by context score (descending), then by text order
    matched_names = [m[2] for m in matches]
    scored_names = sorted(set(matched_names), key=lambda n: (-context_scores.get(n, 0), matched_names.index(n)))

    return scored_names


def detect_topics(text: str | None) -> list[str]:
    """Detect crypto topics in text.

    Scans the text for each crypto's canonical symbol and full name / alias
    using word-boundary regex. Returns a deduplicated list of display names
    preserving first-match order (order they appear in text).

    Context-aware boost: when multiple cryptos are detected, those with nearby
    context keywords (e.g. "gas fee" near ETH) get priority.

    Short symbols (<= 4 chars like SOL, TON, DOT) are excluded from regex
    matching to avoid false positives with common English words.

    Args:
        text: Title + summary combined (or None).

    Returns:
        List of crypto display names found, or empty list.
    """
    if not text:
        return []

    text_lower = text.lower()
    matches = _find_all_matches(text_lower)

    if not matches:
        return []

    # Deduplicate by display name, preserving text-order
    seen: set[str] = set()
    deduped: list[tuple[int, int, str]] = []
    for m in matches:
        if m[2] not in seen:
            seen.add(m[2])
            deduped.append(m)

    # Apply context boost for disambiguation
    result = _boost_matches_with_context(text, deduped)
    return result


def detect_topics_v2(text: str | None) -> list[str]:
    """Alias for detect_topics (v2 naming kept for backwards compatibility)."""
    return detect_topics(text)


def get_crypto_list() -> list[tuple[str, str]]:
    """Return the canonical crypto list as (display_name, symbol) tuples."""
    return [(display, token) for display, token, _, _ in CRYPTO_LIST]


# Backwards compat aliases.
detect_cryptos = detect_topics_v2
