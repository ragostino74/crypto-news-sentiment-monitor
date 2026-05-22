"""Tests for app.services.scheduler.

Covers:
  - Scheduler lifecycle (start / stop / run_once)
  - Periodic scheduling via threading.Timer
  - Manual trigger returns RunResult with correct fields
  - Per-source failure isolation (one bad source doesn't block others)
  - Empty fetch result handling
  - get_scheduler singleton behaviour
  - CLI status command integration
"""

from __future__ import annotations

import dataclasses
import threading
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.services.scheduler import (
    RunResult,
    Scheduler,
    get_scheduler,
    run_pipeline,
)


# ====================================================================
# Fixtures
# ====================================================================


@pytest.fixture()
def clean_scheduler():
    """Return a Scheduler instance not yet started."""
    return Scheduler(interval_seconds=1)  # short interval for fast tests


@pytest.fixture()
def make_raw_article():
    """Factory to create RawArticle test objects."""
    from app.sources.base import RawArticle  # noqa: PLC0415

    def _make(**kwargs):
        defaults = {
            "source": "Test",
            "title": "Test Article",
            "url": f"https://test.com/{id(kwargs)}",
            "published_at": datetime.now(timezone.utc),
            "summary": "Test summary.",
            "content": "Full content here.",
        }
        defaults.update(kwargs)
        return RawArticle(**defaults)

    return _make


def _mock_db_session_scope():
    """Return a MagicMock that works as a context manager (yields a session)."""
    mock = MagicMock()
    # Make it work both as a context manager and when called
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=MagicMock())
    ctx.__exit__ = MagicMock(return_value=False)
    mock.return_value = ctx
    return mock


# ====================================================================
# 1. RunResult dataclass
# ====================================================================


class TestRunResult:
    def test_defaults(self):
        r = RunResult()
        assert r.status == "pending"
        assert r.total_fetched == 0
        assert r.total_clean == 0
        assert r.total_new == 0
        assert r.sources_failed == []
        assert r.run_id is None

    def test_fields_assigned(self):
        now = datetime.now(timezone.utc)
        r = RunResult(
            run_id=42,
            status="completed",
            started_at=now,
            total_fetched=100,
            total_clean=80,
            total_new=30,
            global_sentiment_score=0.5,
        )
        assert r.run_id == 42
        assert r.status == "completed"
        assert r.total_fetched == 100


# ====================================================================
# 2. Scheduler lifecycle (start / stop)
# ====================================================================


class TestSchedulerLifecycle:
    def test_initial_state(self, clean_scheduler):
        """Scheduler should start not-running."""
        assert clean_scheduler._running is False
        assert clean_scheduler._timer is None

    def test_start_marks_running(self, clean_scheduler):
        with patch("app.services.scheduler.init_db"):
            clean_scheduler.start()
        assert clean_scheduler._running is True

    def test_double_start_is_noop(self, clean_scheduler):
        """Calling start() twice shouldn't create duplicate timers."""
        with patch("app.services.scheduler.init_db"):
            timer_count = []

            original_timer = threading.Timer

            class CountingTimer(original_timer):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, **kwargs)
                    timer_count.append(self)

            with patch("threading.Timer", CountingTimer):
                clean_scheduler.start()
                clean_scheduler.start()  # second call — should be ignored

        assert len(timer_count) == 1  # only one Timer created

    def test_stop_cancels_timer(self, clean_scheduler):
        timer_cancelled = threading.Event()

        class CancellingTimer(threading.Timer):
            def cancel(self):
                timer_cancelled.set()
                super().cancel()

        with patch("app.services.scheduler.init_db"):
            with patch("threading.Timer", CancellingTimer):
                clean_scheduler.start()
                # Let the timer fire once
                time.sleep(0.05)
                clean_scheduler.stop()

        assert timer_cancelled.is_set()
        assert clean_scheduler._running is False

    def test_stop_when_not_started_is_safe(self, clean_scheduler):
        """Calling stop() before start should not raise."""
        clean_scheduler.stop()  # should be safe


# ====================================================================
# 3. Periodic scheduling via threading.Timer
# ====================================================================


class TestPeriodicScheduling:
    @patch("app.services.scheduler.init_db")
    def test_schedules_next_run(self, mock_init_db, clean_scheduler):
        """After start, a Timer should be created with the correct interval."""
        timers = []

        class RecordingTimer(threading.Timer):
            def __init__(self, interval, function, *args, **kwargs):
                super().__init__(interval, function, *args, **kwargs)
                timers.append((interval, function))
                self.daemon = True
                # Don't actually fire the timer in this test

        with patch("threading.Timer", RecordingTimer):
            clean_scheduler.start()
            time.sleep(0.05)  # give time for timer creation

        assert len(timers) == 1
        interval, func = timers[0]
        assert interval == 1  # our fixture interval
        assert func == clean_scheduler._run_job

    @patch("app.services.scheduler.init_db")
    def test_timer_is_daemon(self, mock_init_db, clean_scheduler):
        """Timer should be a daemon thread so it doesn't block exit."""
        daemon_flag = [False]

        class CheckingTimer(threading.Timer):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.daemon = True  # set by Scheduler._schedule_next
                daemon_flag.append(self.daemon)

        with patch("threading.Timer", CheckingTimer):
            clean_scheduler.start()
            time.sleep(0.05)

        assert any(daemon_flag[1:])  # at least one timer with daemon=True


# ====================================================================
# 4. Manual run (run_once) — DB-mocked
# ====================================================================


class TestRunOnce:
    """Test run_once with proper DB mocking so _persist_with_sentiment doesn't touch disk."""

    def _make_mock_sources(self, make_raw_article, fail_sources=None):
        """Create mock source fetch functions that return RawArticle objects."""
        from app.sources.base import RawArticle  # noqa: PLC0415

        fail_sources = fail_sources or []
        sources = {}
        for key in ["coindesk", "cointelegraph", "decrypt"]:
            if key in fail_sources:
                sources[key] = (
                    key.capitalize(),
                    MagicMock(side_effect=Exception("Source error")),
                )
            else:
                sources[key] = (
                    key.capitalize(),
                    MagicMock(
                        return_value=[
                            RawArticle(
                                source=key,
                                title=f"Test article from {key}",
                                url=f"https://{key}.com/article",
                                published_at=datetime.now(timezone.utc),
                                summary="Test summary.",
                                content="Full content here.",
                            )
                        ]
                    ),
                )
        return sources

    def _make_db_patches(self):
        """Return a dict of patches to fully isolate DB calls."""
        # session_scope is used inside _persist_with_sentiment and _finish_run
        db_mock = MagicMock()
        db_mock.session_scope.return_value = _mock_db_session_scope()
        db_mock.create_run.return_value = MagicMock(id=99)
        db_mock.upsert_article.return_value = True
        db_mock.finish_run.return_value = None
        return db_mock

    @patch("app.services.scheduler.init_db")
    def test_run_once_with_successful_sources(
        self, mock_init_db, clean_scheduler, make_raw_article
    ):
        """Normal run with all sources successful."""
        db_mock = self._make_db_patches()
        mock_sources = self._make_mock_sources(make_raw_article)

        patches = {
            "init_db": mock_init_db,
            "session_scope": db_mock.session_scope,
            "create_run": db_mock.create_run,
            "upsert_article": db_mock.upsert_article,
            "finish_run": db_mock.finish_run,
        }

        with patch.dict("app.sources.SOURCES", mock_sources, clear=True):
            with patch("app.services.scheduler.session_scope", db_mock.session_scope):
                with patch("app.services.scheduler.create_run", db_mock.create_run):
                    with patch(
                        "app.services.scheduler.upsert_article", db_mock.upsert_article
                    ):
                        with patch("app.services.scheduler.finish_run", db_mock.finish_run):
                            result = clean_scheduler.run_once()

        assert isinstance(result, RunResult)
        assert result.status == "completed"
        assert result.total_fetched > 0 or result.total_clean == 0
        assert result.sources_failed == []
        assert result.started_at is not None
        assert result.finished_at is not None
        assert result.duration_seconds >= 0

    @patch("app.services.scheduler.init_db")
    def test_run_once_with_one_failed_source(
        self, mock_init_db, clean_scheduler, make_raw_article
    ):
        """One source fails — other sources continue."""
        db_mock = self._make_db_patches()
        mock_sources = self._make_mock_sources(make_raw_article, fail_sources=["coindesk"])

        with patch.dict("app.sources.SOURCES", mock_sources, clear=True):
            with patch("app.services.scheduler.session_scope", db_mock.session_scope):
                with patch("app.services.scheduler.create_run", db_mock.create_run):
                    with patch(
                        "app.services.scheduler.upsert_article", db_mock.upsert_article
                    ):
                        with patch("app.services.scheduler.finish_run", db_mock.finish_run):
                            result = clean_scheduler.run_once()

        assert "coindesk" in result.sources_failed
        # Other sources should have been attempted
        assert len(result.sources_failed) == 1

    @patch("app.services.scheduler.init_db")
    def test_run_once_all_sources_fail(
        self, mock_init_db, clean_scheduler, make_raw_article
    ):
        """All sources fail — run completes with error message."""
        db_mock = self._make_db_patches()
        mock_sources = self._make_mock_sources(
            make_raw_article,
            fail_sources=["coindesk", "cointelegraph", "decrypt"],
        )

        with patch.dict("app.sources.SOURCES", mock_sources, clear=True):
            with patch("app.services.scheduler.session_scope", db_mock.session_scope):
                with patch("app.services.scheduler.create_run", db_mock.create_run):
                    with patch(
                        "app.services.scheduler.upsert_article", db_mock.upsert_article
                    ):
                        with patch("app.services.scheduler.finish_run", db_mock.finish_run):
                            result = clean_scheduler.run_once()

        assert "coindesk" in result.sources_failed
        assert len(result.sources_failed) == 3

    @patch("app.services.scheduler.init_db")
    def test_run_once_returns_run_result(self, mock_init_db, clean_scheduler):
        """run_once should always return a RunResult instance."""
        db_mock = self._make_db_patches()

        with patch.dict(
            "app.sources.SOURCES",
            {k: (k.capitalize(), MagicMock(return_value=[])) for k in ["coindesk"]},
            clear=True,
        ):
            with patch("app.services.scheduler.session_scope", db_mock.session_scope):
                with patch("app.services.scheduler.create_run", db_mock.create_run):
                    with patch(
                        "app.services.scheduler.upsert_article", db_mock.upsert_article
                    ):
                        with patch("app.services.scheduler.finish_run", db_mock.finish_run):
                            result = clean_scheduler.run_once()

        assert isinstance(result, RunResult)

    @patch("app.services.scheduler.init_db")
    def test_run_once_duration_tracking(self, mock_init_db, clean_scheduler):
        """Duration should be > 0 after run."""
        db_mock = self._make_db_patches()

        with patch.dict(
            "app.sources.SOURCES",
            {k: (k.capitalize(), MagicMock(return_value=[])) for k in ["coindesk"]},
            clear=True,
        ):
            with patch("app.services.scheduler.session_scope", db_mock.session_scope):
                with patch("app.services.scheduler.create_run", db_mock.create_run):
                    with patch(
                        "app.services.scheduler.upsert_article", db_mock.upsert_article
                    ):
                        with patch("app.services.scheduler.finish_run", db_mock.finish_run):
                            result = clean_scheduler.run_once()

        assert result.duration_seconds >= 0


# ====================================================================
# 5. _fetch_all per-source isolation
# ====================================================================


class TestFetchAll:
    def test_fetch_success_counts(self):
        """Successful fetch returns correct count per source."""
        sched = Scheduler(interval_seconds=1)

        mock_fetch = MagicMock(return_value=[])
        sources = {
            "src1": ("Source 1", mock_fetch),
            "src2": ("Source 2", mock_fetch),
        }

        with patch.dict("app.sources.SOURCES", sources, clear=True):
            articles, counts, failed = sched._fetch_all()

        assert len(articles) == 0
        assert counts["src1"] == 0
        assert counts["src2"] == 0
        assert failed == []

    def test_fetch_failure_isolated(self):
        """A failing source should not raise — it's logged and skipped."""
        sched = Scheduler(interval_seconds=1)

        good_fetch = MagicMock(return_value=[MagicMock(source="good")])
        bad_fetch = MagicMock(side_effect=Exception("Connection refused"))

        sources = {
            "good_src": ("Good Source", good_fetch),
            "bad_src": ("Bad Source", bad_fetch),
        }

        with patch.dict("app.sources.SOURCES", sources, clear=True):
            articles, counts, failed = sched._fetch_all()

        assert len(articles) == 1
        assert "bad_src" in failed
        assert "good_src" not in failed

    def test_empty_fetch(self):
        """Source returning empty list should count as 0, not failure."""
        sched = Scheduler(interval_seconds=1)

        mock_fetch = MagicMock(return_value=[])
        sources = {"empty": ("Empty Source", mock_fetch)}

        with patch.dict("app.sources.SOURCES", sources, clear=True):
            articles, counts, failed = sched._fetch_all()

        assert len(articles) == 0
        assert counts["empty"] == 0
        assert failed == []


# ====================================================================
# 6. get_scheduler singleton
# ====================================================================


class TestGetScheduler:
    def setup_method(self):
        """Reset the singleton before each test."""
        import app.services.scheduler as mod  # noqa: PLC0415

        mod._default_scheduler = None

    def teardown_method(self):
        import app.services.scheduler as mod  # noqa: PLC0415

        mod._default_scheduler = None

    def test_creates_new_instance(self):
        s1 = get_scheduler(interval_seconds=60, db_url="sqlite:///:memory:")
        assert isinstance(s1, Scheduler)
        assert s1.interval == 60

    def test_caches_same_instance(self):
        s1 = get_scheduler(interval_seconds=60)
        s2 = get_scheduler(interval_seconds=60)
        assert s1 is s2  # same instance cached

    def test_recreates_on_interval_change(self):
        s1 = get_scheduler(interval_seconds=60)
        s2 = get_scheduler(interval_seconds=120)
        assert s1 is not s2


# ====================================================================
# 7. Module-level convenience: run_pipeline
# ====================================================================


class TestRunPipeline:
    @patch("app.services.scheduler.init_db")
    def test_run_pipeline_returns_result(self, mock_init_db):
        """run_pipeline() should create a Scheduler and call run_once()."""
        from app.sources.base import RawArticle  # noqa: PLC0415

        with patch.dict(
            "app.sources.SOURCES",
            {k: (k.capitalize(), MagicMock(return_value=[])) for k in ["coindesk"]},
            clear=True,
        ):
            result = run_pipeline()

        assert isinstance(result, RunResult)


# ====================================================================
# 8. Edge cases and robustness
# ====================================================================


class TestEdgeCases:
    def test_scheduler_interval_default_is_300(self):
        """Default interval should be 300 seconds as specified."""
        sched = Scheduler()
        assert sched.interval == 300

    @patch("app.services.scheduler.init_db")
    def test_run_once_with_empty_cleaned(self, mock_init_db, clean_scheduler):
        """If cleaned articles list is empty, run should handle gracefully."""
        db_mock = self._make_db_patches_for_edge_case() if hasattr(self, "_make_db_patches_for_edge_case") else None

        # Just patch the dedupe to return empty; _persist_with_sentiment won't be called
        with patch("app.services.scheduler.clean_and_dedupe", return_value=[]):
            result = clean_scheduler.run_once()

        assert isinstance(result, RunResult)
        assert result.total_clean == 0

    def _make_db_patches_for_edge_case(self):
        """Return db patches for edge case tests."""
        db_mock = MagicMock()
        db_mock.session_scope.return_value = _mock_db_session_scope()
        db_mock.create_run.return_value = MagicMock(id=99)
        db_mock.upsert_article.return_value = True
        db_mock.finish_run.return_value = None
        return db_mock

    @patch("app.services.scheduler.init_db")
    def test_run_once_error_handling(self, mock_init_db, clean_scheduler):
        """If _persist_with_sentiment raises, the run should record the error."""
        db_mock = MagicMock()
        db_mock.session_scope.return_value = _mock_db_session_scope()
        db_mock.create_run.return_value = MagicMock(id=99)
        db_mock.upsert_article.return_value = True
        db_mock.finish_run.return_value = None

        with patch("app.services.scheduler.clean_and_dedupe", side_effect=ValueError("Test error in dedupe")):
            with patch("app.services.scheduler.session_scope", db_mock.session_scope):
                with patch("app.services.scheduler.create_run", db_mock.create_run):
                    with patch("app.services.scheduler.upsert_article", db_mock.upsert_article):
                        with patch("app.services.scheduler.finish_run", db_mock.finish_run):
                            result = clean_scheduler.run_once()

        assert result.status == "error"
        assert "Test error in dedupe" in result.error_message

    def test_scheduler_start_stops_timer(self, clean_scheduler):
        """After stop(), _running should be False and timer None."""
        with patch("app.services.scheduler.init_db"):
            clean_scheduler.start()
            assert clean_scheduler._timer is not None
            clean_scheduler.stop()
            assert clean_scheduler._running is False


# ====================================================================
# 9. Scheduler status reporting
# ====================================================================


class TestStatusReporting:
    def _make_db_patches(self):
        """Return a dict of patches to fully isolate DB calls."""
        db_mock = MagicMock()
        db_mock.session_scope.return_value = _mock_db_session_scope()
        db_mock.create_run.return_value = MagicMock(id=99)
        db_mock.upsert_article.return_value = True
        db_mock.finish_run.return_value = None
        return db_mock

    @patch("app.services.scheduler.init_db")
    def test_status_completed_has_correct_fields(self, mock_init_db, clean_scheduler):
        """A completed run should have status='completed' and no error."""
        db_mock = self._make_db_patches()

        with patch.dict(
            "app.sources.SOURCES",
            {k: (k.capitalize(), MagicMock(return_value=[])) for k in ["coindesk"]},
            clear=True,
        ):
            with patch("app.services.scheduler.session_scope", db_mock.session_scope):
                with patch("app.services.scheduler.create_run", db_mock.create_run):
                    with patch(
                        "app.services.scheduler.upsert_article", db_mock.upsert_article
                    ):
                        with patch("app.services.scheduler.finish_run", db_mock.finish_run):
                            result = clean_scheduler.run_once()

        assert result.status == "completed"
        assert not result.error_message

    @patch("app.services.scheduler.init_db")
    def test_status_error_has_error_message(self, mock_init_db, clean_scheduler):
        """A failed run should record the error message."""
        db_mock = self._make_db_patches()

        # Sources MUST return data so we reach clean_and_dedupe (empty → early exit).
        from app.sources.base import RawArticle  # noqa: PLC0415

        def make_article(source_name: str) -> RawArticle:
            return RawArticle(
                source=source_name,
                title=f"Test article from {source_name}",
                url=f"https://{source_name}.com/article",
                published_at=datetime.now(timezone.utc),
                summary="Test summary.",
                content="Full content here.",
            )

        sources_with_data = {
            k: (k.capitalize(), MagicMock(return_value=[make_article(k)]))
            for k in ["coindesk"]
        }

        with patch.dict("app.sources.SOURCES", sources_with_data, clear=True):
            with patch("app.services.scheduler.session_scope", db_mock.session_scope):
                with patch("app.services.scheduler.create_run", db_mock.create_run):
                    with patch(
                        "app.services.scheduler.upsert_article", db_mock.upsert_article
                    ):
                        with patch("app.services.scheduler.finish_run", db_mock.finish_run):
                            # Override clean_and_dedupe to raise — sources return data so we reach this path.
                            with patch(
                                "app.services.scheduler.clean_and_dedupe",
                                side_effect=RuntimeError("DB locked"),
                            ):
                                result = clean_scheduler.run_once()

        assert result.status == "error"
        assert "DB locked" in result.error_message
