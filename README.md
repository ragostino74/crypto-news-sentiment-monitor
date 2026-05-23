# crypto-news-sentiment-monitor

Backend modulare per la raccolta e analisi di notizie crypto da 13 fonti RSS, con sentiment analysis a doppio motore (VADER + FinBERT).

## Architettura

| Modulo | Descrizione |
|--------|-------------|
|| `sources` | Raccolta RSS da 13 fonti crypto con normalizzazione e gestione redirect (The Defiant) ||
| `services` | Pulizia, normalizzazione testuale e deduplica a due fasi (URL + content-hash) |
| `database` | Persistenza SQLite via SQLAlchemy; tabelle `articles` e `runs` |
| `sentiment` | **Doppio motore**: VADER (rule-based) + FinBERT (Transformer finetuned su testo finanziario). Ensemble ponderato 70/30 per analisi automatica. Endpoint `/api/compare-sentiment` per comparazione side-by-side |
| `topics` | Rilevamento di 55 criptovalute in headline e contenuto |
| `scheduler` | Job periodico (default 300s); per-source failure isolation |
| `api` | Endpoint FastAPI: `/api/articles`, `/api/sentiment`, `/api/crypto-sentiment`, `/api/compare-sentiment`, `/api/runs`, `/api/crypto-topics` |
| `dashboard` | Dashboard web con tabella articoli, card sentiment, grafici per-crypto, refresh auto 60s |

## Fonti (13)

CoinDesk · Cointelegraph · Decrypt · CryptoSlate · CoinJournal · AMBCrypto · Bitcoinist · CryptoPotato · BeInCrypto · NewsBTC · CryptoNews · The Defiant · CoinCentral

## Criptovalute monitorate (55)

Bitcoin, Ethereum, BNB, XRP, Solana, Cardano, Dogecoin, Toncoin, TRON, Avalanche, Shiba Inu, Polygon, Polkadot, Chainlink, Litecoin, Bitcoin Cash, Uniswap, NEAR Protocol, Stellar, Internet Computer, Hedera, Cosmos, Optimism, Arbitrum, Filecoin, Aave, VeChain, Algorand, Immutable, Kaspa, Pendle, Lido DAO, Render, Injective, Celo, Fantom, The Graph, Maker, Theta Network, Flow, Quant, Aptos, Celestia, Sui, Sei, Stacks, MultiversX, Enjin Coin, Flare, Mina, Helium

## Sentiment Analysis Engines

| Motore | Tipo | Vantaggi | Svantaggi |
|--------|------|----------|-----------|
| **VADER** | Rule-based (lexicon) | Nessuna dipendenza ML, istantaneo, leggero | Ignora contesto, pessimo su slang crypto ("moon", "HODL") |
| **FinBERT** | Transformer (ProsusAI/finbert) | Comprendе contesto finanziario e slang crypto | Richiede ~400MB modello, CPU inferenza lenta (~50ms/article) |
| **Ensemble** | 70% FinBERT + 30% VADER | Robustezza massima | Doppia elaborazione |

## Installazione e avvio

### Docker (consigliato)

```bash
# Build e avvio
docker compose build
docker compose up -d
```

L'immagine scarica automaticamente il modello FinBERT dal primo avvio e lo cache in `/root/.cache/huggingface`. I volumi `sqlite-data` e `hf-cache` persistono tra i riavvii.

### Installazione locale

```bash
git clone https://github.com/ragostino74/crypto-news-sentiment-monitor.git
cd crypto-news-sentiment-monitor
python3 -m venv venv && source venv/bin/activate

# Solo VADER (nessuna dipendenza ML)
pip install -r requirements.txt

# Oppure con FinBERT (richiede torch + transformers)
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt

pytest                        # test (unit + live integration)
python -m app.main            # scheduler in background
python -m app.main web        # dashboard + API su localhost:8000
```

## Testing

```bash
pytest                              # unit + live integration (143 test)
pytest -m "not integration"         # solo unit test
pytest -m integration               # solo live integration test
```

## API Endpoints

| Endpoint | Metodo | Descrizione |
|----------|--------|-------------|
| `/` | GET | Dashboard web |
| `/api/articles` | GET | Articoli recenti (query: `?limit=200&source=CoinDesk&topic=bitcoin`) |
| `/api/sentiment` | GET | Agregati sentiment globali e per fonte |
| `/api/crypto-sentiment` | GET | Sentiment aggregato per criptovaluta (`?crypto=bitcoin`) |
| `/api/compare-sentiment` | GET | **Confronto VADER vs FinBERT** sullo stesso testo (`?title=...&summary=...&content=...`) |
| `/api/crypto-topics` | GET | Lista delle 55 criptovalute supportate |
| `/api/runs` | GET | Storico esecuzioni pipeline (`?limit=10`) |

### Esempio: confronta VADER vs FinBERT

```bash
curl "http://localhost:8000/api/compare-sentiment?\
title=Bitcoin+surges+past+100K\
&summary=ETF+inflows+hit+record+highs"
```

Risposta:
```json
{
  "vader":     { "label": "positive",    "compound": 0.1027 },
  "finbert":   { "label": "positive",    "compound": 0.9011 }
}
```

FinBERT è significativamente più sensibile al sentiment positivo in testo crypto/finanziario rispetto a VADER (che viene diluito dalle stop words neutrali).

## CLI

```bash
python -m app.main                # scheduler + web server (modalità completa)
python -m app.main run            # esecuzione pipeline singola
python -m app.main status         # statistics ultima esecuzione
python -m app.main web            # solo dashboard + API (senza scheduler)
```

## Configurazione

| Variabile | Default | Descrizione |
|-----------|---------|-------------|
| `RSS_INTERVAL` | `300` | Intervallo tra esecuzioni pipeline (secondi) |
| `DB_URL` | `sqlite:///data/crypto.db` | URL database SQLite |
| `LOG_LEVEL` | `INFO` | Livello logging |

## Struttura progetto

```
app/
  core/         Database init, session management
  models/       SQLAlchemy ORM (Article, Run)
  sources/      RSS fetchers: base, coindesk, cointelegraph, decrypt, cryptoslate, coinjournal, ambcrypto, bitcoinist, cryptopotato, beincrypto, newsbtc, cryptonews, thedefiant, coincentral
  services/     Dedupe, topics detection, sentiment analysis (VADER + FinBERT + Ensemble)
  static/       CSS, JS dashboard
  templates/    Jinja2 HTML templates
main.py         Entry point (CLI + web server bootstrap)
docker-compose.yml  base + finbert service definitions
Dockerfile      Container build (Python 3.11-slim)
tests/          143 test (pytest, unit + live integration)
```

## License

MIT
