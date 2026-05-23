"""Crypto news sources package.

Provides a unified registry of source adapters (13 verified crypto RSS sources).
Each adapter inherits from SourceAdapter ABC and implements fetch_latest().
"""

from .base import RawArticle, fetch_rss_feed, strip_html_tags, normalize_summary
from .coindesk import fetch_latest as fetch_coindesk_latest
from .cointelegraph import fetch_latest as fetch_cointelegraph_latest
from .decrypt import fetch_latest as fetch_decrypt_latest
from .cryptoslate import fetch_latest as fetch_cryptoslate_latest
from .coinjournal import fetch_latest as fetch_coinjournal_latest
from .ambcrypto import fetch_latest as fetch_ambcrypto_latest
from .bitcoinist import fetch_latest as fetch_bitcoinist_latest
from .cryptopotato import fetch_latest as fetch_cryptopotato_latest
from .beincrypto import fetch_latest as fetch_beincrypto_latest
from .newsbtc import fetch_latest as fetch_newsbtc_latest
from .cryptonews import fetch_latest as fetch_cryptonews_latest
from .thedefiant import fetch_latest as fetch_thedefiant_latest
from .coincentral import fetch_latest as fetch_coincentral_latest

# Registry of verified RSS source adapters (13 sources).
SOURCES = {
    "coindesk": ("CoinDesk", fetch_coindesk_latest),
    "cointelegraph": ("Cointelegraph", fetch_cointelegraph_latest),
    "decrypt": ("Decrypt", fetch_decrypt_latest),
    "cryptoslate": ("CryptoSlate", fetch_cryptoslate_latest),
    "coinjournal": ("CoinJournal", fetch_coinjournal_latest),
    "ambcrypto": ("AMBCrypto", fetch_ambcrypto_latest),
    "bitcoinist": ("Bitcoinist", fetch_bitcoinist_latest),
    "cryptopotato": ("CryptoPotato", fetch_cryptopotato_latest),
    "beincrypto": ("BeInCrypto", fetch_beincrypto_latest),
    "newsbtc": ("NewsBTC", fetch_newsbtc_latest),
    "cryptonews": ("CryptoNews", fetch_cryptonews_latest),
    "thedefiant": ("The Defiant", fetch_thedefiant_latest),
    "coincentral": ("CoinCentral", fetch_coincentral_latest),
}

__all__ = [
    "RawArticle",
    "fetch_rss_feed",
    "strip_html_tags",
    "normalize_summary",
    "SOURCES",
    "fetch_coindesk_latest",
    "fetch_cointelegraph_latest",
    "fetch_decrypt_latest",
    "fetch_cryptoslate_latest",
    "fetch_coinjournal_latest",
    "fetch_ambcrypto_latest",
    "fetch_bitcoinist_latest",
    "fetch_cryptopotato_latest",
    "fetch_beincrypto_latest",
    "fetch_newsbtc_latest",
    "fetch_cryptonews_latest",
    "fetch_thedefiant_latest",
    "fetch_coincentral_latest",
]
