# crypto-news-sentiment-monitor

Backend modulare per la raccolta e analisi di notizie crypto da multiple fonti RSS.

## Architettura

Il progetto è suddiviso in **moduli indipendenti**, ciascuno completabile e testabile separatamente:

| Modulo | Stato | Descrizione |
|--------|-------|-------------|
| `sources` | ✅ Pronto | Raccolta notizie da 5+ fonti RSS con normalizzazione |
| `services` | ✅ Pronto | Pulizia, normalizzazione e deduplica degli articoli |
| `database` | 🔲 Da fare | Persistenza articoli e risultati sentiment |
| `sentiment` | ✅ Pronto | Analisi sentiment VADER su headline e summary |
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

## Modulo Services (attuale)

### Cosa fa

- **Normalizzazione testuale**: collassa whitespace, normalizza Unicode (NFC), pulisce campi `title`, `source`, `summary`, `content`
- **URL canonicalization**: HTTPS forzato per domini crypto conosciuti, lowercase host, rimozione `/index.html`, slash trailing, parametri query ordinati alfabeticamente, rimozione tracker (`utm_*`)
- **Deduplica a due fasi**:
  1. Per URL canonico (primario) — primo visto vince
  2. Per content-hash `title + source + published_at + summary` (fallback per URL non affidabili o mirror)

### Funzioni principali

```python
from app.services.dedupe import clean_and_dedupe, deduplicate_only
from app.services.normalization import canonicalize_url, normalize_text_field

# Pipeline completa: normalizza + pulisci + deduplica
articles = fetch_latest(limit=20)  # from any source
cleaned = clean_and_dedupe(articles)

# Solo deduplica URL (senza fallback content-hash)
cleaned = deduplicate_only(articles)

# Singola funzione di normalizzazione URL
canonical = canonicalize_url("http://coindesk.com/news?utm_source=twitter&a=1")
# → "https://coindesk.com/news?a=1"
```

### Dati in output

I `RawArticle` vengono convertiti in `CleanedArticle`:

```python
@dataclass(slots=True)
class CleanedArticle:
    source: str
    title: str
    url: str
    published_at: datetime | None
    summary: str          # max 500 caratteri, whitespace pulito
    content: str          # max 5000 caratteri, whitespace pulito
    canonical_url: str    # URL canonico per riferimento
```

---

## Modulo Sentiment (attuale)

### Cosa fa

- **Analisi sentiment VADER** su testo composto da titolo + summary + excerpt content
- Classificazione in tre categorie usando soglie standard:
  - `compound >= 0.05` → **positive**
  - `compound <= -0.05` → **negative**
  - altrimenti → **neutral**
- Costruzione del testo da analizzare preservando punteggiatura e maiuscole (segnali chiave per VADER)
- Aggregazione globale: media compound, label dominante, conteggi per categoria
- Interfaccia `SentimentEngine` (Protocol) per futura sostituzione con transformer/LLM

### Funzioni principali

```python
from app.services.sentiment import (
    VaderSentimentEngine,
    build_sentiment_text,
    analyze_sentiment,
    classify_vader,
    aggregate_sentiment,
)

# Costruisci il testo da analizzare
text = build_sentiment_text(title="Bitcoin crashes!", summary="BTC drops 15%")

# Analizza un singolo articolo (CleanedArticle duck-type)
result, text_used = analyze_sentiment(article)
print(result.sentiment_label)      # "negative"
print(result.sentiment_compound)   # -0.3129
print(result.sentiment_engine)     # "vader"

# Classificazione one-shot
label = classify_vader(-0.31)      # "negative"

# Aggrega risultati multipli
stats = aggregate_sentiment([r1, r2, r3])
# { mean_compound: -0.12, global_label: "negative",
#   count_positive: 1, count_neutral: 1, count_negative: 1, total: 3 }

# Risultato per articolo
@dataclass(slots=True)
class SentimentResult:
    sentiment_neg: float
    sentiment_neu: float
    sentiment_pos: float
    sentiment_compound: float       # -1.0 .. +1.0
    sentiment_label: str            # "positive" | "neutral" | "negative"
    sentiment_engine: str           # "vader" (default)
```

### Soglie VADER

| Score | Categoria | Descrizione |
|-------|-----------|-------------|
| ≥ +0.05 | positive | Sentimento favorevole |
| ≤ −0.05 | negative | Sentimento sfavorevole |
| (−0.05, +0.05) | neutral | Neutro / incerto |

---

## Installazione

### Requisiti

- Python 3.11+
- Dipendenze: `httpx`, `feedparser`, `beautifulsoup4`, `lxml`, `vaderSentiment`, `pytest`

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

**101 test totali:** 29 source test (22 unit + 3 integration live + 4 error handling) + 39 services test (whitespace, URL canonicalization, normalize_article, URL dedup, content-hash fallback, filtering, edge cases) + 33 sentiment test (VADER classification, aggregation, text building).

---

## Roadmap

- [ ] Modulo database con modelli articoli e sentiment
- [x] Modulo sentiment (analisi headline/summary con VADER)
- [ ] API FastAPI con endpoint `/articles`, `/sources`, `/sentiment`
- [ ] Scheduler per raccolta periodica
- [ ] Dashboard web di visualizzazione
- [ ] Docker support

---

## License

MIT
