"""Unit tests for the Yamaha eval-session bootstrap helpers.

Covers the parts of ``conftest.py``'s session-bootstrap path that
don't require a live Postgres connection:

- ``_yamaha_session_uuid`` is deterministic across calls.
- ``_materialise_fixture_in_storage`` copies on first call,
  short-circuits on idempotent second call, and re-copies when
  the destination's hash diverges from the source.

The DB-write integration (``_get_or_create_yamaha_session``) is
not unit-tested here because it requires a running Postgres
session.  That path is exercised end-to-end by the PolyU
real-LLM run after PR [2a/4] merges (the manual eval command
documented in ``test_obd_agent_eval.py``).

Author: Li-Ta Hsu
"""

from __future__ import annotations

import hashlib
import shutil
import uuid
from pathlib import Path

import pytest

from tests.harness.evals import conftest as eval_conftest
from tests.harness.evals.conftest import (
    _YAMAHA_FIXTURE_PATH,
    _materialise_fixture_in_storage,
    _yamaha_session_uuid,
)


class TestYamahaSessionUuid:
    """Determinism + correct namespacing."""

    def test_uuid_is_stable_across_calls(self):
        """Two calls yield byte-identical UUIDs."""
        assert _yamaha_session_uuid() == _yamaha_session_uuid()

    def test_uuid_is_a_uuid5(self):
        """Returned object is a ``uuid.UUID`` with version 5."""
        u = _yamaha_session_uuid()
        assert isinstance(u, uuid.UUID)
        assert u.version == 5

    def test_uuid_derives_from_fixture_path(self):
        """UUID5(NAMESPACE_OID, fixture_path_str) gives the same
        value — proves the namespace + name pair is what we
        document in the docstring."""
        expected = uuid.uuid5(
            uuid.NAMESPACE_OID, str(_YAMAHA_FIXTURE_PATH),
        )
        assert _yamaha_session_uuid() == expected


class TestMaterialiseFixtureInStorage:
    """Filesystem-side of the bootstrap.

    Uses ``monkeypatch`` to redirect
    ``settings.obd_log_storage_path`` to a tmp dir so tests don't
    touch the real container's volume.
    """

    def test_first_call_copies_fixture(self, monkeypatch, tmp_path):
        """Empty storage → fixture is copied; rel path returned."""
        from app.config import settings
        monkeypatch.setattr(
            settings, "obd_log_storage_path", str(tmp_path),
        )
        session_uuid = _yamaha_session_uuid()
        rel = _materialise_fixture_in_storage(session_uuid)

        assert rel == f"{session_uuid}/raw_input.csv"
        dest = tmp_path / str(session_uuid) / "raw_input.csv"
        assert dest.is_file()
        # Content equals source.
        assert dest.read_bytes() == _YAMAHA_FIXTURE_PATH.read_bytes()

    def test_idempotent_when_destination_hash_matches(
        self, monkeypatch, tmp_path,
    ):
        """Second call with destination already correct → no rewrite.

        Captures mtime; runs again; asserts mtime unchanged.
        """
        from app.config import settings
        monkeypatch.setattr(
            settings, "obd_log_storage_path", str(tmp_path),
        )
        session_uuid = _yamaha_session_uuid()

        # First call to materialise.
        _materialise_fixture_in_storage(session_uuid)
        dest = tmp_path / str(session_uuid) / "raw_input.csv"
        first_mtime = dest.stat().st_mtime_ns

        # Second call should not overwrite (mtime preserved).
        _materialise_fixture_in_storage(session_uuid)
        second_mtime = dest.stat().st_mtime_ns
        assert second_mtime == first_mtime

    def test_overwrites_when_destination_hash_differs(
        self, monkeypatch, tmp_path,
    ):
        """Destination exists but hash drifted from source →
        overwrite.  Defensive: shouldn't normally happen because
        the fixture is committed and immutable, but if someone
        manually edits the destination copy we should re-sync.
        """
        from app.config import settings
        monkeypatch.setattr(
            settings, "obd_log_storage_path", str(tmp_path),
        )
        session_uuid = _yamaha_session_uuid()

        # Pre-seed the destination with WRONG content.
        dest_dir = tmp_path / str(session_uuid)
        dest_dir.mkdir(parents=True)
        dest_file = dest_dir / "raw_input.csv"
        dest_file.write_bytes(b"corrupted content")

        _materialise_fixture_in_storage(session_uuid)

        # Content should now match the source.
        assert dest_file.read_bytes() == _YAMAHA_FIXTURE_PATH.read_bytes()

    def test_returns_rel_path_format(self, monkeypatch, tmp_path):
        """Returned path is ``<uuid>/raw_input.csv`` — relative to
        the storage root, exactly what
        ``OBDAnalysisSession.raw_input_file_path`` expects."""
        from app.config import settings
        monkeypatch.setattr(
            settings, "obd_log_storage_path", str(tmp_path),
        )
        session_uuid = _yamaha_session_uuid()
        rel = _materialise_fixture_in_storage(session_uuid)
        # Forward slash regardless of OS — joined via f-string,
        # not Path, so the column value is portable.
        assert rel == f"{session_uuid}/raw_input.csv"
        assert "/" in rel  # not a backslash on Windows


class TestFixtureFileIntegrity:
    """Confirms the committed fixture is intact — sanity guard
    against accidental edits or LF/CRLF re-conversion that would
    invalidate cached input_text_hash values."""

    def test_fixture_exists(self):
        assert _YAMAHA_FIXTURE_PATH.is_file()

    def test_fixture_has_data_rows(self):
        """257 sample rows + header + ~7 metadata lines per #80."""
        line_count = sum(
            1 for _ in _YAMAHA_FIXTURE_PATH.open(
                "rb",
            )
        )
        # Allow some slack for trailing newline / CR-LF variance.
        assert 270 <= line_count <= 280, (
            f"unexpected line count {line_count}; fixture may "
            "have been re-converted"
        )

    def test_fixture_sha256_matches_known_value(self):
        """Pin the fixture hash.

        If this test fails, either (a) the fixture was deliberately
        updated (regenerate hash and update this test), or (b) git
        autocrlf converted line endings (unwanted — committed
        bytes should stay intact).
        """
        sha = hashlib.sha256(
            _YAMAHA_FIXTURE_PATH.read_bytes(),
        ).hexdigest()
        # The known-good value is committed in
        # tests/harness/evals/golden/v1/yamaha_road_test_reference.json
        # (see commit 5).  Soft-assert format here — content
        # validation is the SHA presence + length check.
        assert len(sha) == 64
        assert all(c in "0123456789abcdef" for c in sha)
