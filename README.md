# crypto-news-sentiment-monitor

Backend modulare per la raccolta e analisi di notizie crypto da 10 fonti RSS.

## Architettura

| Modulo | Descrizione |
|--------|-------------|
| `sources` | Raccolta RSS da 10 fonti crypto con normalizzazione |
| `services` | Pulizia, normalizzazione testuale e deduplica a due fasi (URL + content-hash) |
| `database` | Persistenza SQLite via SQLAlchemy; tabelle `articles` e `runs` |
| `sentiment` | Analisi VADER su headline+summary; aggregazione globale per fonte |
| `scheduler` | Job periodico (default 300s); per-source failure isolation |
| `api` | Endpoint FastAPI: `/api/articles`, `/api/sentiment`, `/api/runs` |
| `dashboard` | Dashboard web con tabella articoli, card sentiment, refresh auto 60s |

## Fonti (10)

CoinDesk · Cointelegraph · Decrypt · The Block · CryptoSlate · CoinJournal · AMBCrypto · Bitcoinist · CryptoPotato · BeInCrypto

## Installazione e avvio

```bash
git clone https://github.com/ragostino74/crypto-news-sentiment-monitor.git
cd crypto-news-sentiment-monitor
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
pytest                        # test (unit + live integration)
python -m app.main            # scheduler in background
python -m app.main web        # dashboard + API su localhost:8000
```

Docker: `docker compose up -d` (volume `sqlite-data` persiste il DB).

## Testing

```bash
pytest                              # unit + live
pytest -m "not integration"         # solo unit
pytest -m integration               # solo live
```

143 test totali.

## License

MIT
