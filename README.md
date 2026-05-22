# crypto-news-sentiment-monitor

Backend modulare per la raccolta e analisi di notizie crypto da multiple fonti RSS.

## Architettura

Il progetto è suddiviso in **moduli indipendenti**, ciascuno completabile e testabile separatamente:

| Modulo | Stato | Descrizione |
|--------|-------|-------------|
| `sources` | ✅ Pronto | Raccolta notizie da 5+ fonti RSS con normalizzazione |
| `database` | 🔲 Da fare | Persistenza articoli e risultati sentiment |
| `sentiment` | 🔲 Da fare | Analisi sentiment su headline e summary |
| `api` | 🔲 Da fare | Endpoint FastAPI per consumo dati |
| `scheduler` | 🔲 Da fare | Raccolta periodica degli articoli |

---

## Modulo Sources (attuale)

### Cosa fa

- Raccoglie notizie RSS da **5 fonti crypto** in parallelo
- Normalizza tutti gli articoli in una struttura `RawArticle` unica
- Ogni fonte è isolata: un fallimento non compromette le altre
- Timeout HTTP e User-Agent custom per ogni richiesta

### Fonti supportate

| Fonte | Feed RSS | Articoli stimati |
|-------|----------|-----------------|
| CoinDesk | `feeds.feedburner.com/coindesk` | ~25 |
| Cointelegraph | `cointelegraph.com/rss` | ~30 |
| Decrypt | `decrypt.co/feed` | ~56 |
| The Block | `www.theblock.co/rss.xml` | ~19 |
| CryptoSlate | `cryptoslate.com/feed/` | ~10 |

### Struttura dati

```python
@dataclass
class RawArticle:
    source: str           # Nome della fonte
    title: str            # Headline dell'articolo
    url: str              # URL originale
    published_at: Optional[datetime]  # Data di pubblicazione
    summary: str          # Riassunto / snippet
    content: str          # Corpo completo (se disponibile)
```

---

## Installazione

### Requisiti

- Python 3.11+
- Dipendenze: `httpx`, `feedparser`, `beautifulsoup4`, `lxml`, `pytest`

### Setup

```bash
# Clona il repo
git clone https://github.com/ragostino74/crypto-news-sentiment-monitor.git
cd crypto-news-sentiment-monitor

# Crea un virtual environment
python3 -m venv venv
source venv/bin/activate

# Installa le dipendenze
pip install -r requirements.txt

# Esegui i test
pytest
```

---

## Utilizzo

### Fetch da una singola fonte

```python
from app.sources.coindesk import fetch_latest

articles = fetch_latest(limit=10)
for a in articles:
    print(f"[{a.source}] {a.title} ({a.published_at})")
```

### Fetch da tutte le fonti

```python
from app.sources import SOURCES

for name, (label, fetch_fn) in SOURCES.items():
    articles = fetch_fn(limit=5)
    print(f"{label}: {len(articles)} articoli")
```

---

## Testing

```bash
# Tutti i test (unit + integration live)
pytest

# Solo unit test (nessuna rete richiesta)
pytest -m "not integration"

# Solo test live (richiede rete)
pytest -m integration
```

**29 test totali:** 22 unit test + 3 integration test + 4 test di error handling.

---

## Roadmap

- [ ] Modulo database con modelli articoli e sentiment
- [ ] Modulo sentiment (analisi headline/summary)
- [ ] API FastAPI con endpoint `/articles`, `/sources`, `/sentiment`
- [ ] Scheduler per raccolta periodica
- [ ] Dashboard web di visualizzazione
- [ ] Docker support

---

## License

MIT
