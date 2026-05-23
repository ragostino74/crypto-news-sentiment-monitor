"""Tests for app.services.topics — crypto topic detection."""

from __future__ import annotations

import pytest  # noqa: TID251

from app.services.topics import (
    CRYPTO_LIST,
    detect_topics_v2,
    get_crypto_list,
)


class TestGetCryptoList:
    """Verify the canonical crypto list is well-formed."""

    def test_returns_20_entries(self):
        result = get_crypto_list()
        assert len(result) == 55

    def test_each_entry_is_tuple(self):
        for name, symbol in get_crypto_list():
            assert isinstance(name, str) and name
            assert isinstance(symbol, str) and symbol

    def test_bitcoin_present(self):
        names = [name for name, _ in get_crypto_list()]
        assert "Bitcoin" in names

    def test_no_duplicate_symbols(self):
        symbols = [sym for _, sym in get_crypto_list()]
        assert len(symbols) == len(set(symbols))


class TestDetectTopicsV2:
    """Verify topic detection against title/summary combinations."""

    # --- Bitcoin (BTC) ---
    def test_detects_bitcoin_by_full_name(self):
        assert detect_topics_v2("Bitcoin reaches new high") == ["Bitcoin"]

    def test_detects_bitcoin_by_symbol(self):
        assert detect_topics_v2("BTC breaks $100k resistance") == ["Bitcoin"]

    def test_no_false_positive_on_btc_in_word(self):
        # "BTC" should match, but a random word containing it should not
        result = detect_topics_v2("atc is a store name")
        assert "Bitcoin" not in result

    # --- Ethereum (ETH) ---
    def test_detects_ethereum_by_full_name(self):
        assert detect_topics_v2("Ethereum 2.0 staking rewards") == ["Ethereum"]

    def test_detects_ethereum_by_symbol(self):
        assert detect_topics_v2("ETH gas fees drop to 5 gwei") == ["Ethereum"]

    # --- Multiple cryptos ---
    def test_detects_multiple_cryptos(self):
        text = "Bitcoin and Ethereum both rally as BTC breaks ATH"
        result = detect_topics_v2(text)
        assert "Bitcoin" in result
        assert "Ethereum" in result

    def test_preserves_order_of_first_match(self):
        text = "Ethereum goes up then Bitcoin follows"
        result = detect_topics_v2(text)
        assert result.index("Ethereum") < result.index("Bitcoin")

    # --- Edge cases ---
    def test_empty_string(self):
        assert detect_topics_v2("") == []

    def test_none_input(self):
        assert detect_topics_v2(None) == []

    def test_no_crypto_mentioned(self):
        result = detect_topics_v2("The stock market is down today")
        assert len(result) == 0

    def test_case_insensitive(self):
        text = "BITCOIN and ETHEREUM surge"
        result = detect_topics_v2(text)
        assert "Bitcoin" in result
        assert "Ethereum" in result

    # --- Ripple / XRP ---
    def test_detects_ripple_alias(self):
        assert detect_topics_v2("Ripple wins SEC case") == ["XRP"]

    # --- Polygon alias check ---
    def test_detects_polygon_by_symbol(self):
        result = detect_topics_v2("MATIC drops below $1")
        assert "Polygon" in result

    # --- Dogecoin / SHIB ---
    def test_detects_dogecoin_by_full_name(self):
        assert detect_topics_v2("Dogecoin community raises for charity") == ["Dogecoin"]

    def test_detects_shiba_inu_by_symbol(self):
        assert detect_topics_v2("SHIB burns 1B tokens") == ["Shiba Inu"]

    # --- No false positive on common words ---
    def test_no_false_positive_on_coin_word(self):
        # "coin" appears in many contexts but should not trigger anything
        result = detect_topics_v2("the coin market is volatile")
        assert len(result) == 0

    def test_no_false_positive_on_link_word(self):
        result = detect_topics_v2("please link the document")
        assert "Chainlink" not in result

    # --- Tether / USDT ---
    def test_detects_tether(self):
        result = detect_topics_v2("Tether mints 1B USDT")
        assert "Tether" in result
