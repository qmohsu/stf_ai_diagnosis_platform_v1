"""Tests for per-page conversion progress reporting (APP-46).

Covers four layers of the progress pipeline:

1. The Alembic migration ``s3t4u5v6w7x8`` adds the two new
   columns to the ``manuals`` table.
2. ``_sync_progress_to_db`` reads ``{id}.progress.json`` from
   the queue dir and updates the DB row.
3. ``_ProgressReporter`` (in ``scripts/marker_worker.py``)
   throttles writes and uses atomic rename.
4. ``ManualStatusResponse`` and ``ManualSummary`` serialize the
   new fields.
"""

import importlib.util
import json
import sys
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── Helpers to import the worker module without the marker
# package being installed ──

_REPO_ROOT = Path(__file__).resolve().parents[3]
_WORKER_PATH = (
    _REPO_ROOT / "diagnostic_api" / "scripts" / "marker_worker.py"
)


def _load_marker_worker():
    """Load ``marker_worker.py`` as an isolated module.

    The CI environment doesn't ship ``marker-pdf``, so we
    cannot ``import scripts.marker_worker`` normally — its
    top-level imports try to bring in marker.  Instead we use
    importlib to evaluate just the module body; the marker
    import lives inside ``_process_request`` and isn't
    triggered by simply loading the file.
    """
    spec = importlib.util.spec_from_file_location(
        "marker_worker_under_test", str(_WORKER_PATH),
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ── 1. Migration ────────────────────────────────────────────


class TestMigration:
    """Tests for the ``s3t4u5v6w7x8`` migration metadata."""

    def test_revision_chains_after_r2s3(self):
        """The migration's down_revision is r2s3t4u5v6w7."""
        spec = importlib.util.spec_from_file_location(
            "_mig_s3t4",
            str(
                _REPO_ROOT
                / "diagnostic_api"
                / "alembic"
                / "versions"
                / "s3t4_add_manual_progress_columns.py"
            ),
        )
        mig = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mig)
        assert mig.revision == "s3t4u5v6w7x8"
        assert mig.down_revision == "r2s3t4u5v6w7"

    def test_upgrade_adds_two_columns(self):
        """upgrade() invokes add_column twice on 'manuals'."""
        spec = importlib.util.spec_from_file_location(
            "_mig_s3t4",
            str(
                _REPO_ROOT
                / "diagnostic_api"
                / "alembic"
                / "versions"
                / "s3t4_add_manual_progress_columns.py"
            ),
        )
        mig = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mig)

        added: list[tuple[str, str]] = []
        with patch.object(
            mig.op, "add_column",
            side_effect=lambda table, col: added.append(
                (table, col.name),
            ),
        ):
            mig.upgrade()

        names = [name for _, name in added]
        assert "pages_processed" in names
        assert "pages_total" in names
        assert all(t == "manuals" for t, _ in added)


# ── 2. _sync_progress_to_db ─────────────────────────────────


class TestSyncProgressToDb:
    """Tests for the API-side progress poller."""

    def test_returns_none_when_progress_file_missing(
        self, tmp_path,
    ):
        """No file on disk → no-op, returns None."""
        from app.services.manual_pipeline import (
            _sync_progress_to_db,
        )
        progress_path = str(tmp_path / "missing.progress.json")
        log = MagicMock()
        result = _sync_progress_to_db(
            progress_path,
            uuid.uuid4(),
            last_processed=-1,
            log=log,
        )
        assert result is None

    def test_returns_none_on_malformed_json(self, tmp_path):
        """Half-written JSON is treated as transient, returns None."""
        from app.services.manual_pipeline import (
            _sync_progress_to_db,
        )
        progress_path = tmp_path / "bad.progress.json"
        progress_path.write_text(
            "{not valid", encoding="utf-8",
        )
        result = _sync_progress_to_db(
            str(progress_path),
            uuid.uuid4(),
            last_processed=-1,
            log=MagicMock(),
        )
        assert result is None

    def test_returns_none_on_missing_fields(self, tmp_path):
        """Payload missing 'processed' or 'total' is ignored."""
        from app.services.manual_pipeline import (
            _sync_progress_to_db,
        )
        progress_path = tmp_path / "p.progress.json"
        progress_path.write_text(
            json.dumps({"processed": 5}), encoding="utf-8",
        )
        result = _sync_progress_to_db(
            str(progress_path),
            uuid.uuid4(),
            last_processed=-1,
            log=MagicMock(),
        )
        assert result is None

    def test_skips_when_processed_unchanged(self, tmp_path):
        """If processed == last_processed, no DB write."""
        from app.services.manual_pipeline import (
            _sync_progress_to_db,
        )
        progress_path = tmp_path / "p.progress.json"
        progress_path.write_text(
            json.dumps({"processed": 7, "total": 10}),
            encoding="utf-8",
        )
        with patch(
            "app.services.manual_pipeline.SessionLocal",
        ) as mock_session_factory:
            result = _sync_progress_to_db(
                str(progress_path),
                uuid.uuid4(),
                last_processed=7,
                log=MagicMock(),
            )
        assert result is None
        mock_session_factory.assert_not_called()

    def test_updates_db_when_processed_advances(
        self, tmp_path,
    ):
        """Writing to DB is the side-effect on a new tick."""
        from app.services.manual_pipeline import (
            _sync_progress_to_db,
        )
        progress_path = tmp_path / "p.progress.json"
        progress_path.write_text(
            json.dumps({"processed": 12, "total": 20}),
            encoding="utf-8",
        )

        manual = MagicMock()
        manual.pages_processed = None
        manual.pages_total = None

        mock_db = MagicMock()
        mock_db.query.return_value.get.return_value = manual

        manual_id = uuid.uuid4()
        with patch(
            "app.services.manual_pipeline.SessionLocal",
            return_value=mock_db,
        ):
            result = _sync_progress_to_db(
                str(progress_path),
                manual_id,
                last_processed=5,
                log=MagicMock(),
            )

        assert result == 12
        assert manual.pages_processed == 12
        assert manual.pages_total == 20
        mock_db.commit.assert_called_once()
        mock_db.close.assert_called_once()

    def test_returns_none_when_manual_row_missing(
        self, tmp_path,
    ):
        """Race: row deleted mid-conversion → no crash."""
        from app.services.manual_pipeline import (
            _sync_progress_to_db,
        )
        progress_path = tmp_path / "p.progress.json"
        progress_path.write_text(
            json.dumps({"processed": 1, "total": 5}),
            encoding="utf-8",
        )
        mock_db = MagicMock()
        mock_db.query.return_value.get.return_value = None
        with patch(
            "app.services.manual_pipeline.SessionLocal",
            return_value=mock_db,
        ):
            result = _sync_progress_to_db(
                str(progress_path),
                uuid.uuid4(),
                last_processed=-1,
                log=MagicMock(),
            )
        assert result is None
        mock_db.close.assert_called_once()


# ── 3. _ProgressReporter (worker-side throttle) ────────────


class TestProgressReporter:
    """Tests for the worker-side atomic + throttled writer."""

    def test_first_forced_write_creates_file(self, tmp_path):
        """force=True bypasses the throttle on the first call."""
        worker = _load_marker_worker()
        progress_path = tmp_path / "x.progress.json"
        rep = worker._ProgressReporter(progress_path)
        rep.report(processed=3, total=10, force=True)
        assert progress_path.is_file()
        payload = json.loads(progress_path.read_text())
        assert payload == {"processed": 3, "total": 10}

    def test_phase_field_included_when_set(self, tmp_path):
        """A non-empty phase is serialized into the payload."""
        worker = _load_marker_worker()
        progress_path = tmp_path / "x.progress.json"
        rep = worker._ProgressReporter(progress_path)
        rep.report(
            processed=10, total=10, phase="done", force=True,
        )
        payload = json.loads(progress_path.read_text())
        assert payload["phase"] == "done"

    def test_throttle_blocks_second_immediate_write(
        self, tmp_path,
    ):
        """Two writes within 1.5s without force → only first sticks."""
        worker = _load_marker_worker()
        progress_path = tmp_path / "x.progress.json"
        rep = worker._ProgressReporter(progress_path)
        rep.report(processed=1, total=10, force=True)
        first_payload = json.loads(progress_path.read_text())
        rep.report(processed=2, total=10)  # not forced
        second_payload = json.loads(progress_path.read_text())
        # The second call advances the page counter but is still
        # throttled by elapsed-time, so the file content stays
        # at the first write.
        assert first_payload == second_payload

    def test_no_write_when_page_unchanged(self, tmp_path):
        """Reporting the same page twice is a no-op."""
        worker = _load_marker_worker()
        progress_path = tmp_path / "x.progress.json"
        rep = worker._ProgressReporter(progress_path)
        rep.report(processed=5, total=10, force=True)
        mtime_first = progress_path.stat().st_mtime_ns
        # Wait long enough that the throttle would otherwise allow
        # another write, but reuse the same processed value.
        time.sleep(0.05)
        rep.report(processed=5, total=10)
        mtime_second = progress_path.stat().st_mtime_ns
        assert mtime_first == mtime_second

    def test_atomic_rename_via_tmp_path(self, tmp_path):
        """No leftover .tmp file after a successful write."""
        worker = _load_marker_worker()
        progress_path = tmp_path / "x.progress.json"
        rep = worker._ProgressReporter(progress_path)
        rep.report(processed=1, total=2, force=True)
        # The reporter writes ``.tmp`` (note: ``with_suffix``
        # replaces ``.json`` with ``.tmp`` on the
        # ``x.progress.json`` path → ``x.progress.tmp``).
        leftover = tmp_path / "x.progress.tmp"
        assert not leftover.exists()


# ── 4. tqdm hook ────────────────────────────────────────────


class TestTqdmHook:
    """Tests for ``_install_tqdm_hook`` monkey-patching."""

    def test_no_tqdm_returns_none(self):
        """If tqdm is missing, install is a graceful no-op."""
        worker = _load_marker_worker()

        class _FakeReporter:
            def report(self, **_kwargs):
                pass

        # Hide tqdm temporarily.
        saved = sys.modules.get("tqdm")
        sys.modules["tqdm"] = None  # type: ignore[assignment]
        try:
            with patch.dict(
                sys.modules, {"tqdm": None}, clear=False,
            ):
                # ``import tqdm`` will raise ImportError because
                # the cached value is None.  Use a builtin
                # __import__ shim so the worker sees ImportError.
                import builtins
                real_import = builtins.__import__

                def _shim(name, *args, **kwargs):
                    if name == "tqdm":
                        raise ImportError(
                            "tqdm hidden for test",
                        )
                    return real_import(name, *args, **kwargs)

                with patch.object(
                    builtins, "__import__", side_effect=_shim,
                ):
                    result = worker._install_tqdm_hook(
                        _FakeReporter(),
                    )
            assert result is None
        finally:
            if saved is None:
                sys.modules.pop("tqdm", None)
            else:
                sys.modules["tqdm"] = saved

    def test_tqdm_update_reports_progress(self):
        """Each tqdm.update tick mirrors to the reporter."""
        pytest.importorskip("tqdm")
        import io
        worker = _load_marker_worker()

        captured: list[tuple[int, int]] = []

        class _FakeReporter:
            def report(self, processed, total, **_kwargs):
                captured.append((processed, total))

        original = worker._install_tqdm_hook(_FakeReporter())
        try:
            import tqdm
            # Real progress bar (disable=True short-circuits
            # update() on some tqdm versions, leaving n=0).
            # Send the bar's output to /dev/null so the test
            # doesn't pollute stderr.
            sink = io.StringIO()
            bar = tqdm.tqdm(total=4, file=sink, mininterval=0)
            bar.update(1)
            bar.update(1)
            bar.update(1)
            bar.close()
        finally:
            worker._restore_tqdm_hook(original)

        # Three updates seen, totals are 4 throughout.
        assert (1, 4) in captured
        assert (2, 4) in captured
        assert (3, 4) in captured

    def test_restore_idempotent_when_install_skipped(self):
        """restore(None) is a safe no-op."""
        worker = _load_marker_worker()
        worker._restore_tqdm_hook(None)  # no exception


# ── 5. ManualStatusResponse / ManualSummary serialization ──


class TestStatusResponseSerialization:
    """Tests for the new fields on the API response models."""

    def test_status_response_includes_progress_fields(self):
        """ManualStatusResponse exposes pages_processed/total."""
        from app.api.v2.endpoints.manuals import (
            ManualStatusResponse,
        )
        resp = ManualStatusResponse(
            status="converting",
            error_message=None,
            page_count=None,
            chunk_count=None,
            pages_processed=3,
            pages_total=10,
        )
        dumped = resp.model_dump()
        assert dumped["pages_processed"] == 3
        assert dumped["pages_total"] == 10

    def test_status_response_progress_fields_optional(self):
        """Both fields default to None for older callers."""
        from app.api.v2.endpoints.manuals import (
            ManualStatusResponse,
        )
        resp = ManualStatusResponse(
            status="ingested",
            error_message=None,
            page_count=42,
            chunk_count=100,
        )
        assert resp.pages_processed is None
        assert resp.pages_total is None

    def test_summary_includes_progress_fields(self):
        """_to_summary surfaces pages_processed / pages_total."""
        from app.api.v2.endpoints.manuals import _to_summary
        from datetime import datetime, timezone
        manual = MagicMock()
        manual.id = uuid.uuid4()
        manual.filename = "x.pdf"
        manual.vehicle_model = "TRICITY-155"
        manual.status = "converting"
        manual.file_size_bytes = 1024
        manual.page_count = None
        manual.section_count = None
        manual.language = None
        manual.chunk_count = None
        manual.pages_processed = 7
        manual.pages_total = 12
        manual.created_at = datetime.now(timezone.utc)
        manual.updated_at = datetime.now(timezone.utc)

        summary = _to_summary(manual)
        assert summary.pages_processed == 7
        assert summary.pages_total == 12
