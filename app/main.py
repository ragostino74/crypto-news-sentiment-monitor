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

# ------------------------------------------------------------------ Logging ---

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


# ------------------------------------------------------------------ Bootstrap ---

def bootstrap(interval_seconds: int = 300, db_url: str | None = None) -> None:
    """Start the full application (scheduler runs until interrupted).

    Args:
        interval_seconds: Seconds between automatic pipeline runs.
        db_url: Optional database URL override.
    """
    _setup_logging()
    logger = logging.getLogger(__name__)

    from app.services.scheduler import get_scheduler  # noqa: PLC0415

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
            import time  # noqa: PLC0415

            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt — stopping scheduler…")
        scheduler.stop()


# ------------------------------------------------------------------ CLI ---

def _cli() -> None:
    """Command-line interface for manual operations."""
    parser = argparse.ArgumentParser(
        description="Crypto News & Sentiment Monitor",
    )
    subparsers = parser.add_subparsers(dest="command")

    # --- run -----------------------------------------------------------
    run_parser = subparsers.add_parser("run", help="Trigger a single pipeline run")
    run_parser.add_argument(
        "--interval", type=int, default=300, help="Scheduler interval (unused for manual runs)"
    )
    run_parser.add_argument("--db-url", type=str, default=None, help="Database URL override")

    # --- status --------------------------------------------------------
    subparsers.add_parser("status", help="Show last run statistics from the DB")

    args = parser.parse_args()

    if args.command == "run":
        from app.services.scheduler import run_pipeline  # noqa: PLC0415

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
        from app.core.db import get_session, get_last_run  # noqa: PLC0415
        from app.models.run import Run  # noqa: PLC0415

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

    else:
        # No subcommand — start the full app (scheduler mode).
        bootstrap()


if __name__ == "__main__":
    _cli()
