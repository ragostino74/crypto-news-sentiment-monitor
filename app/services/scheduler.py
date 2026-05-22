"""Scheduler module for crypto-news-sentiment-monitor.

Provides a periodic job executor that runs the full pipeline:

    fetch -> normalise -> deduplicate -> sentiment -> persist -> run finish

Features:
  - Configurable interval (default 300 s)
  - Per-source failure isolation — a single bad source never stops other sources
  - Manual trigger support for testing / on-demand runs
  - Graceful start / stop with threading.Timer-based scheduling
  - Structured logging at each pipeline stage

Usage:
    # Create and start the scheduler (runs in background thread)
    scheduler = Scheduler(interval_seconds=300, db_url="sqlite:///data/crypto_news.db")
    scheduler.start()

    # Trigger a one-off run manually
    result = scheduler.run_once()

    # Stop the scheduler cleanly
    scheduler.stop()
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

# ------------------------------------------------------------------ Imports ---

from app.core.db import (
    create_run,
    finish_run,
    init_db,
    session_scope,
    upsert_article,
)
from app.services.dedupe import CleanedArticle, clean_and_dedupe
from app.services.sentiment import (
    SentimentResult,
    aggregate_sentiment,
    analyze_sentiment,
)
from app.services.topics import detect_topics_v2

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ Data model ---


@dataclass(slots=True)
class RunResult:
    """Summary of a single pipeline run."""

    run_id: int | None = None
    status: str = "pending"  # completed / failed / error
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_seconds: float = 0.0
    total_fetched: int = 0      # raw articles across all sources
    total_clean: int = 0        # after normalisation + dedup
    total_new: int = 0          # inserted into DB (new)
    total_updated: int = 0      # updated in DB (existing)
    sources_failed: list[str] = field(default_factory=list)
    source_results: dict[str, int] = field(default_factory=dict)
    global_sentiment_score: float | None = None
    global_sentiment_label: str | None = None
    error_message: str | None = None


# ------------------------------------------------------------------ Scheduler ---


class Scheduler:
    """Periodic pipeline executor.

    Schedules a full run every ``interval_seconds`` in a background thread.
    Each run calls the complete pipeline chain (fetch → clean → dedup → sentiment
    → persist) and records statistics in the ``runs`` table.

    Args:
        interval_seconds: Seconds between automatic runs (default 300).
        db_url: Optional SQLite URL override for the DB engine.
    """

    def __init__(
        self,
        interval_seconds: int = 300,
        db_url: str | None = None,
    ) -> None:
        self.interval = interval_seconds
        self.db_url = db_url

        # --- State ----------------------------------------------------------
        self._timer: threading.Timer | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._running = False

    # ------------------------------------------------------------------ Lifecycle ---

    def start(self) -> None:
        """Start the periodic scheduler in a background thread.

        The first run fires after ``interval_seconds`` from this call.
        Calling start() multiple times is safe — subsequent calls are no-ops
        while already running.
        """
        if self._running:
            logger.debug("Scheduler already running — ignoring start()")
            return

        # Initialise DB tables on first start.
        init_db(self.db_url)

        with self._lock:
            self._running = True
            self._stop_event.clear()

        logger.info(
            "Scheduler started — interval=%ds, db=%s",
            self.interval,
            self.db_url or "default",
        )
        self._schedule_next()

    def stop(self) -> None:
        """Stop the scheduler and cancel any pending timer."""
        if not self._running:
            return

        logger.info("Scheduler stopping…")

        with self._lock:
            self._running = False

        # Cancel the next scheduled run.
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

        self._stop_event.set()
        logger.info("Scheduler stopped.")

    def _schedule_next(self) -> None:
        """Schedule the next automatic run after ``interval_seconds``."""
        if not self._running:
            return

        self._timer = threading.Timer(self.interval, self._run_job)
        self._timer.daemon = True  # don't block process exit
        self._timer.start()
        logger.debug("Next run scheduled in %ds", self.interval)

    def _run_job(self) -> None:
        """Execute one pipeline run and reschedule."""
        try:
            self.run_once()
        except Exception:
            logger.exception("Unhandled error during scheduler job")
        finally:
            # Always reschedule — even if the run errored.
            with self._lock:
                if self._running:
                    self._schedule_next()

    # ------------------------------------------------------------------ Manual trigger ---

    def run_once(self) -> RunResult:
        """Execute a single pipeline run immediately.

        This is also called by the automatic scheduler timer, but can be used
        for on-demand runs (e.g. from a CLI command or API endpoint).

        Returns:
            ``RunResult`` with statistics and status.
        """
        # Ensure DB tables exist even outside of start().
        init_db(self.db_url)

        result = RunResult(status="running")
        result.started_at = datetime.now(timezone.utc)

        logger.info("Pipeline run STARTED (run_once)")

        try:
            # --- Phase 1: Fetch from all sources ---------------------------
            raw_articles, source_results, failed_sources = self._fetch_all()
            total_fetched = sum(source_results.values())
            result.total_fetched = total_fetched
            result.sources_failed = failed_sources

            if not raw_articles:
                logger.warning("Pipeline run: no articles fetched from any source")
                result.status = "completed"
                self._finish_run(result, error_message="No articles fetched from any source")
                return result

            # --- Phase 2: Normalise + Deduplicate --------------------------
            result.total_clean = len(clean_and_dedupe(raw_articles))
            logger.info("Pipeline run: %d raw → %d clean", total_fetched, result.total_clean)

            # --- Phase 3: Sentiment analysis + persistence ---------------------------
            sentiment_results, new_count, updated_count, run_id = self._persist_with_sentiment(
                raw_articles,
            )
            result.run_id = run_id
            result.total_new = new_count
            result.total_updated = updated_count

            if sentiment_results:
                agg = aggregate_sentiment(sentiment_results)
                result.global_sentiment_score = agg["mean_compound"]
                result.global_sentiment_label = agg["global_label"]

            result.status = "completed"

        except Exception as exc:
            logger.exception("Pipeline run FAILED")
            result.status = "error"
            result.error_message = str(exc)
            self._finish_run(result, error_message=str(exc))

        finally:
            result.finished_at = datetime.now(timezone.utc)
            if result.started_at and result.finished_at:
                result.duration_seconds = (
                    result.finished_at - result.started_at
                ).total_seconds()

            logger.info(
                "Pipeline run ENDED — status=%s, fetched=%d, clean=%d, new=%d, updated=%d, failed_sources=%s, duration=%.2fs",
                result.status,
                result.total_fetched,
                result.total_clean,
                result.total_new,
                result.total_updated,
                result.sources_failed,
                result.duration_seconds,
            )

        return result

    # ------------------------------------------------------------------ Internal pipeline ---

    def _fetch_all(self):
        """Fetch articles from all registered sources.

        Returns:
            Tuple of (all_raw_articles, {source_name: count}, [failed_source_names]).
        """
        from app.sources import SOURCES  # noqa: PLC0415

        all_articles: list = []
        source_counts: dict[str, int] = {}
        failed_sources: list[str] = []

        for key, (display_name, fetch_fn) in SOURCES.items():
            try:
                articles = fetch_fn()
                count = len(articles) if articles else 0
                source_counts[key] = count
                if articles:
                    all_articles.extend(articles)
                logger.info("Source [%s] (%s): %d articles", key, display_name, count)
            except Exception:
                failed_sources.append(key)
                logger.exception("Source [%s] (%s) FAILED — skipping", key, display_name)

        return all_articles, source_counts, failed_sources

    def _persist_with_sentiment(self, raw_articles):
        """Normalise, deduplicate, analyse sentiment, and persist articles.

        Returns:
            Tuple of (sentiment_results, new_count, updated_count, run_id).
        """
        from app.sources import SOURCES  # noqa: PLC0415

        cleaned = clean_and_dedupe(raw_articles)

        with session_scope() as session:
            run = create_run(
                session,
                status="running",
                articles_fetched=len(raw_articles),
            )
            session.flush()
            run_id = int(getattr(run, "id", 0))

        sentiment_results: list[SentimentResult] = []
        new_count = 0
        updated_count = 0

        with session_scope() as session:
            for article in cleaned:
                # --- Sentiment analysis ---------------------------------------
                sr, _text = analyze_sentiment(article)

                # --- Crypto topic detection -----------------------------------
                combined_text = f"{article.title} {article.summary}"
                detected = detect_topics_v2(combined_text)
                topics_json = str(detected)  # JSON-serialised list of names

                sentiment_results.append(sr)

                # --- Persist to DB --------------------------------------------
                inserted = upsert_article(
                    session,
                    source=article.source,
                    title=article.title,
                    url=article.url,
                    published_at=article.published_at,
                    summary=article.summary,
                    content=article.content,
                    sentiment_pos=sr.sentiment_pos,
                    sentiment_neu=sr.sentiment_neu,
                    sentiment_neg=sr.sentiment_neg,
                    sentiment_compound=sr.sentiment_compound,
                    sentiment_label=sr.sentiment_label,
                    sentiment_engine=sr.sentiment_engine,
                    run_id=run_id,
                    topics=topics_json,
                )
                if inserted:
                    new_count += 1
                else:
                    updated_count += 1

        # --- Finalise the run in DB ---------------------------------------------
        sentiment_results_agg = aggregate_sentiment(sentiment_results) if sentiment_results else {}
        try:
            with session_scope() as session:
                finish_run(
                    session,
                    run_id,
                    articles_new=new_count,
                    global_sentiment_score=sentiment_results_agg.get("mean_compound"),
                    global_sentiment_label=sentiment_results_agg.get("global_label", "neutral"),
                )
        except Exception:
            logger.exception("Failed to finalise run %d in DB", run_id)

        return sentiment_results, new_count, updated_count, run_id

    def _finish_run(self, result: RunResult, error_message: str | None = None) -> None:
        """Mark the run as completed or failed in the database."""
        if result.run_id is None:
            # The run_id wasn't captured (shouldn't happen in normal flow).
            return

        try:
            with session_scope() as session:
                finish_run(
                    session,
                    result.run_id,
                    articles_new=result.total_new,
                    global_sentiment_score=result.global_sentiment_score,
                    global_sentiment_label=result.global_sentiment_label,
                    status="completed" if result.status == "completed" else "failed",
                    error_message=error_message or result.error_message,
                )
        except Exception:
            logger.exception("Failed to update run %d status in DB", result.run_id)


# ------------------------------------------------------------------ Module-level singleton ---

# Default scheduler instance — created on first access for lazy initialisation.
_default_scheduler: Scheduler | None = None


def get_scheduler(
    interval_seconds: int = 300,
    db_url: str | None = None,
) -> Scheduler:
    """Return (or create) the global scheduler singleton.

    Subsequent calls with the same arguments return the cached instance.
    Use this when you want a single scheduler managed at module level.
    """
    global _default_scheduler
    if _default_scheduler is None or (interval_seconds != _default_scheduler.interval):
        _default_scheduler = Scheduler(
            interval_seconds=interval_seconds,
            db_url=db_url,
        )
    return _default_scheduler


def run_pipeline() -> RunResult:
    """Convenience function: trigger the global scheduler for a one-off run.

    Creates the default scheduler (interval=300s), runs once, and returns
    the result. Does NOT start the periodic timer — caller manages lifecycle.
    """
    sched = Scheduler(interval_seconds=300)
    return sched.run_once()


__all__ = [
    "Scheduler",
    "RunResult",
    "get_scheduler",
    "run_pipeline",
]
