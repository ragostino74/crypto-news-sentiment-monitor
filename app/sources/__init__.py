from .base import RawArticle, fetch_rss_feed, strip_html_tags, normalize_summary
from .coindesk import fetch_latest as fetch_coindesk_latest
from .cointelegraph import fetch_latest as fetch_cointelegraph_latest
from .decrypt import fetch_latest as fetch_decrypt_latest
from .theblock import fetch_latest as fetch_theblock_latest
from .cryptoslate import fetch_latest as fetch_cryptoslate_latest

# Registry of all available source adapters.
SOURCES = {
    "coindesk": ("CoinDesk", fetch_coindesk_latest),
    "cointelegraph": ("Cointelegraph", fetch_cointelegraph_latest),
    "decrypt": ("Decrypt", fetch_decrypt_latest),
    "theblock": ("The Block", fetch_theblock_latest),
    "cryptoslate": ("CryptoSlate", fetch_cryptoslate_latest),
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
    "fetch_theblock_latest",
    "fetch_cryptoslate_latest",
]
