"""Entry point for crypto-news-sentiment-monitor.

Bootstraps the application: initialises the database, configures logging and
starts the scheduler in the background.  Also exposes a CLI so you can
trigger one-off pipeline runs from the terminal.

Usage::

    # Run the full app (scheduler starts automatically)
    python -m app.main

    # Trigger a single manual run and print results
    python -m app.main run

    # Show scheduler status
    python -m app.main status
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from pathlib import Path


def _start_web_server(
    host: str = "0.0.0.0",
    port: int = 8000,
    db_url: str | None = None,
) -> None:
    """Start the FastAPI web server with dashboard and API endpoints."""
    from contextlib import asynccontextmanager

    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles
    from jinja2 import Environment, FileSystemLoader

    from app.core.db import (
        get_crypto_sentiment,
        get_latest_articles,
        get_session,
        session_scope,
    )
    from app.models.article import Article
    from app.models.run import Run
    from app.services.topics import get_crypto_list
    import app

    @asynccontextmanager
    async def lifespan(app_instance: FastAPI):
        """Initialise DB on startup."""
        from app.core.db import init_db

        init_db(db_url)
        yield

    web_app = FastAPI(
        title="Crypto News Sentiment Monitor",
        version=app.__version__,
        lifespan=lifespan,
    )

    # Mount static files
    static_dir = Path(__file__).parent / "static"
    web_app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Jinja2 template environment
    env = Environment(loader=FileSystemLoader(str(Path(__file__).parent / "templates")))

    @web_app.get("/", response_class=HTMLResponse)
    async def dashboard():
        template = env.get_template("index.html")
        html = template.render(version=app.__version__)
        return HTMLResponse(content=html)

    @web_app.get("/api/articles")
    async def api_articles(limit: int = 200, source: str | None = None, topic: str | None = None):
        with session_scope() as session:
            articles = get_latest_articles(session, limit=limit, source=source, topic=topic)

            result = []
            for a in articles:
                result.append({
                    "id": getattr(a, "id", None),
                    "source": getattr(a, "source", ""),
                    "title": getattr(a, "title", ""),
                    "url": getattr(a, "url", ""),
                    "published_at": (
                        a.published_at.isoformat() if a.published_at else None
                    ),
                    "sentiment_compound": getattr(a, "sentiment_compound", None),
                    "sentiment_label": getattr(a, "sentiment_label", None),
                    "sentiment_pos": getattr(a, "sentiment_pos", None),
                    "sentiment_neg": getattr(a, "sentiment_neg", None),
                    "sentiment_neu": getattr(a, "sentiment_neu", None),
                    "topics": getattr(a, "topics", ""),
                })
        return JSONResponse(content=result)

    @web_app.get("/api/crypto-topics")
    async def api_crypto_topics():
        """Return the list of supported crypto topics."""
        return JSONResponse(content={
            "cryptos": get_crypto_list(),
        })

    @web_app.get("/api/sentiment")
    async def api_sentiment():
        """Global and per-source sentiment aggregates."""
        from sqlalchemy import select as _select

        with session_scope() as session:
            stmt = _select(Article).where(getattr(Article, "sentiment_compound").is_not(None))
            articles = list(session.execute(stmt).scalars().all())

            compounds = [a.sentiment_compound for a in articles if a.sentiment_compound is not None]
            global_mean = sum(compounds) / len(compounds) if compounds else None

            source_map: dict[str, list[float]] = {}
            for a in articles:
                if a.sentiment_compound is None:
                    continue
                source_map.setdefault(a.source, []).append(a.sentiment_compound)

            sources = []
            for name, vals in sorted(source_map.items()):
                sources.append({
                    "source": name,
                    "count": len(vals),
                    "mean_compound": sum(vals) / len(vals),
                })
            sources.sort(key=lambda s: s["mean_compound"], reverse=True)

        return JSONResponse(content={
            "global_mean_compound": global_mean,
            "total_articles": len(articles),
            "sources": sources,
        })

    @web_app.get("/api/crypto-sentiment")
    async def api_crypto_sentiment(crypto: str):
        """Per-crypto sentiment aggregate."""
        with session_scope() as session:
            data = get_crypto_sentiment(session, crypto, limit=100)
        return JSONResponse(content=data if data else {"error": "No articles found for this crypto"})

    @web_app.get("/api/compare-sentiment")
    async def api_compare_sentiment(
        title: str,
        summary: str = "",
        content: str = "",
    ):
        """Compare VADER vs FinBERT sentiment on the same article text."""
        from app.services.sentiment import compare_sentiment as _compare

        comparison = _compare(title=title, summary=summary, content=content)
        return JSONResponse(content={
            "composite_text": comparison.composite_text[:200] + "...",
            "vader": {
                "label": comparison.vader_label,
                "compound": round(comparison.vader_compound, 4),
                "positive": round(comparison.vader_pos, 4),
                "neutral": round(comparison.vader_neu, 4),
                "negative": round(comparison.vader_neg, 4),
            },
            "finbert": {
                "label": comparison.finbert_label,
                "compound": (
                    round(comparison.finbert_compound, 4)
                    if comparison.finbert_compound is not None else None
                ),
                "positive": (
                    round(comparison.finbert_pos, 4)
                    if comparison.finbert_pos is not None else None
                ),
                "neutral": (
                    round(comparison.finbert_neu, 4)
                    if comparison.finbert_neu is not None else None
                ),
                "negative": (
                    round(comparison.finbert_neg, 4)
                    if comparison.finbert_neg is not None else None
                ),
            },
        })

    @web_app.get("/api/runs")
    async def api_runs(limit: int = 10):
        """Latest pipeline runs."""
        from sqlalchemy import desc as _desc
        from sqlalchemy import select as _select

        with session_scope() as session:
            stmt = _select(Run).order_by(_desc(getattr(Run, "started_at"))).limit(limit)
            runs = list(session.execute(stmt).scalars().all())

            result = []
            for r in runs:
                result.append({
                    "id": getattr(r, "id", None),
                    "started_at": (r.started_at.isoformat() if r.started_at else None),
                    "finished_at": (
                        r.finished_at.isoformat() if r.finished_at else None
                    ),
                    "status": getattr(r, "status", ""),
                    "articles_fetched": getattr(r, "articles_fetched", 0),
                    "articles_new": getattr(r, "articles_new", 0),
                    "global_sentiment_score": getattr(r, "global_sentiment_score", None),
                    "global_sentiment_label": getattr(r, "global_sentiment_label", None),
                })

        return JSONResponse(content=result)

    import uvicorn
    uvicorn.run(web_app, host=host, port=port)


def _setup_logging() -> None:
    """Configure root logger with human-readable format."""
    log_dir = Path("data")
    log_dir.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_dir / "scheduler.log"),
        ],
    )


def bootstrap(interval_seconds: int = 300, db_url: str | None = None) -> None:
    """Start the full application (scheduler runs until interrupted).

    Args:
        interval_seconds: Seconds between automatic pipeline runs.
        db_url: Optional database URL override.
    """
    _setup_logging()
    logger = logging.getLogger(__name__)

    from app.services.scheduler import get_scheduler

    scheduler = get_scheduler(interval_seconds=interval_seconds, db_url=db_url)
    scheduler.start()

    # Graceful shutdown on SIGINT / SIGTERM
    def _shutdown(signum: int, _frame: object) -> None:
        logger.info("Received signal %d — shutting down scheduler…", signum)
        scheduler.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    logger.info(
        "Crypto News & Sentiment Monitor started (interval=%ds). Press Ctrl+C to stop.",
        interval_seconds,
    )

    # Keep the main thread alive so the background timer can fire.
    try:
        while True:
            import time

            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt — stopping scheduler…")
        scheduler.stop()


def _cli() -> None:
    """Command-line interface for manual operations."""
    parser = argparse.ArgumentParser(
        description="Crypto News & Sentiment Monitor",
    )
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Trigger a single pipeline run")
    run_parser.add_argument(
        "--interval", type=int, default=300, help="Scheduler interval (unused for manual runs)"
    )
    run_parser.add_argument("--db-url", type=str, default=None, help="Database URL override")

    subparsers.add_parser("status", help="Show last run statistics from the DB")

    web_parser = subparsers.add_parser("web", help="Start the FastAPI dashboard server")
    web_parser.add_argument("--host", type=str, default="0.0.0.0", help="Bind address")
    web_parser.add_argument("--port", type=int, default=8000, help="Bind port")
    web_parser.add_argument("--db-url", type=str, default=None, help="Database URL override")

    args = parser.parse_args()

    if args.command == "run":
        from app.services.scheduler import run_pipeline

        _setup_logging()
        result = run_pipeline()
        print(f"\n{'='*60}")
        print(f"Run {result.run_id} — {result.status.upper()}")
        print(f"{'='*60}")
        print(f"  Fetched:       {result.total_fetched}")
        print(f"  Clean (post-dedup): {result.total_clean}")
        print(f"  New articles:  {result.total_new}")
        print(f"  Updated:       {result.total_updated}")
        if result.global_sentiment_score is not None:
            print(
                f"  Sentiment:     {result.global_sentiment_label} "
                f"(score={result.global_sentiment_score:.4f})"
            )
        if result.sources_failed:
            print(f"  Failed sources:{', '.join(result.sources_failed)}")
        print(f"{'='*60}\n")

    elif args.command == "status":
        from app.core.db import get_session, get_last_run
        from app.models.run import Run

        with get_session() as session:
            run = get_last_run(session)
            if run is None:
                print("No runs recorded yet.")
                return
            assert isinstance(run, Run), f"Expected Run, got {type(run).__name__}"
            print(f"\n{'='*60}")
            print(f"Last Run #{run.id}")
            print(f"{'='*60}")
            print(f"  Started:       {run.started_at}")
            print(f"  Finished:      {run.finished_at}")
            print(f"  Status:        {run.status}")
            print(f"  Fetched:       {run.articles_fetched}")
            print(f"  New articles:  {run.articles_new or 0}")
            if run.global_sentiment_score is not None:
                print(
                    f"  Sentiment:     {run.global_sentiment_label} "
                    f"(score={run.global_sentiment_score:.4f})"
                )
            if run.error_message:
                print(f"  Error:         {run.error_message}")
            print(f"{'='*60}\n")

    elif args.command == "web":
        _start_web_server(
            host=args.host,
            port=args.port,
            db_url=args.db_url,
        )

    else:
        bootstrap()


if __name__ == "__main__":
    _cli()
