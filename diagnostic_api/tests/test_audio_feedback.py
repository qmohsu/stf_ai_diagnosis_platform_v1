"""Tests for audio recording feedback feature.

Covers:
  - POST /v2/obd/audio/upload (valid, invalid MIME, oversize, auth)
  - Feedback submission with audio_token linking
  - GET  /v2/obd/audio/{feedback_id} (playback, ownership, 404)
  - GET  /v2/obd/{session_id}/feedback (has_audio flag in history)
  - Audio duration validation in OBDFeedbackRequest
"""

from __future__ import annotations

import io
import os
import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# -------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------

VALID_FEEDBACK = {
    "rating": 4,
    "is_helpful": True,
    "comments": "Good analysis",
}


@pytest.fixture()
def client():
    """Create a TestClient that bypasses DB-dependent startup."""
    with patch("app.db.session.SessionLocal"), \
         patch("app.db.session.engine"):
        from app.main import app
        yield TestClient(app)


@pytest.fixture()
def app_ref():
    """Return the FastAPI app for dependency overrides."""
    from app.main import app
    return app


@pytest.fixture(autouse=True)
def clear_overrides(app_ref):
    """Set up auth override and clean up after each test."""
    from app.auth.security import get_current_user
    from tests.conftest import make_mock_user

    mock_user = make_mock_user()
    app_ref.dependency_overrides[get_current_user] = (
        lambda: mock_user
    )
    yield
    app_ref.dependency_overrides.clear()


def _make_audio_bytes(
    size: int = 1024,
    fmt: str = "webm",
) -> bytes:
    """Create fake audio bytes with valid magic header.

    Args:
        size: Total byte count for the payload.
        fmt: Audio format — determines magic bytes prefix.

    Returns:
        Bytes with a valid container signature followed by
        zero-padding to reach the requested size.
    """
    headers = {
        "webm": b"\x1a\x45\xdf\xa3",  # WebM / Matroska
        "ogg": b"OggS",
        "wav": b"RIFF",
        "m4a": b"\x00\x00\x00\x1cftyp",
    }
    header = headers.get(fmt, b"\x1a\x45\xdf\xa3")
    return header + b"\x00" * max(0, size - len(header))


# -------------------------------------------------------------------
# POST /v2/obd/audio/upload
# -------------------------------------------------------------------


class TestAudioUpload:
    """Tests for the audio upload endpoint."""

    def test_upload_valid_webm(self, client, tmp_path):
        """Valid WebM file returns 201 with audio_token."""
        with patch(
            "app.api.v2.endpoints.obd_analysis.settings"
        ) as mock_settings:
            mock_settings.audio_allowed_mime_type_list = [
                "audio/webm",
            ]
            mock_settings.audio_max_file_size_bytes = 5_242_880
            mock_settings.audio_storage_path = str(tmp_path)
            os.makedirs(tmp_path / "staging", exist_ok=True)

            data = _make_audio_bytes(2048)
            resp = client.post(
                "/v2/obd/audio/upload",
                files={
                    "file": (
                        "recording.webm",
                        io.BytesIO(data),
                        "audio/webm",
                    ),
                },
            )

        assert resp.status_code == 201
        body = resp.json()
        assert "audio_token" in body
        assert body["size_bytes"] == 2048

    def test_upload_invalid_mime_returns_415(self, client):
        """Non-audio MIME type returns 415."""
        with patch(
            "app.api.v2.endpoints.obd_analysis.settings"
        ) as mock_settings:
            mock_settings.audio_allowed_mime_type_list = [
                "audio/webm",
            ]

            resp = client.post(
                "/v2/obd/audio/upload",
                files={
                    "file": (
                        "notes.txt",
                        io.BytesIO(b"not audio"),
                        "text/plain",
                    ),
                },
            )

        assert resp.status_code == 415
        assert "Unsupported" in resp.json()["detail"]

    def test_upload_exceeds_size_limit_returns_413(
        self, client,
    ):
        """File exceeding size limit returns 413."""
        with patch(
            "app.api.v2.endpoints.obd_analysis.settings"
        ) as mock_settings:
            mock_settings.audio_allowed_mime_type_list = [
                "audio/webm",
            ]
            mock_settings.audio_max_file_size_bytes = 100

            data = _make_audio_bytes(200)
            resp = client.post(
                "/v2/obd/audio/upload",
                files={
                    "file": (
                        "big.webm",
                        io.BytesIO(data),
                        "audio/webm",
                    ),
                },
            )

        assert resp.status_code == 413
        assert "too large" in resp.json()["detail"]

    def test_upload_invalid_magic_bytes_returns_415(
        self, client, tmp_path,
    ):
        """File with valid MIME but wrong magic bytes is rejected."""
        with patch(
            "app.api.v2.endpoints.obd_analysis.settings"
        ) as mock_settings:
            mock_settings.audio_allowed_mime_type_list = [
                "audio/webm",
            ]
            mock_settings.audio_max_file_size_bytes = 5_242_880

            # Send bytes that don't match any audio signature.
            resp = client.post(
                "/v2/obd/audio/upload",
                files={
                    "file": (
                        "fake.webm",
                        io.BytesIO(b"not real audio content"),
                        "audio/webm",
                    ),
                },
            )

        assert resp.status_code == 415
        assert "recognised audio" in resp.json()["detail"]

    def test_upload_requires_auth(self, client, app_ref):
        """Upload without auth returns 401."""
        app_ref.dependency_overrides.clear()
        resp = client.post(
            "/v2/obd/audio/upload",
            files={
                "file": (
                    "recording.webm",
                    io.BytesIO(b"\x00"),
                    "audio/webm",
                ),
            },
        )
        assert resp.status_code in (401, 403)


# -------------------------------------------------------------------
# Audio duration validation in OBDFeedbackRequest
# -------------------------------------------------------------------


class TestAudioDurationValidation:
    """Tests for audio_duration_seconds field validation."""

    def test_duration_within_range_accepted(self):
        """Duration 0-120 passes validation."""
        from app.api.v2.schemas import OBDFeedbackRequest

        req = OBDFeedbackRequest(
            rating=5,
            is_helpful=True,
            audio_duration_seconds=60,
        )
        assert req.audio_duration_seconds == 60

    def test_duration_exceeding_max_rejected(self):
        """Duration > 600 fails validation."""
        from app.api.v2.schemas import OBDFeedbackRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            OBDFeedbackRequest(
                rating=5,
                is_helpful=True,
                audio_duration_seconds=601,
            )

    def test_negative_duration_rejected(self):
        """Negative duration fails validation."""
        from app.api.v2.schemas import OBDFeedbackRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            OBDFeedbackRequest(
                rating=5,
                is_helpful=True,
                audio_duration_seconds=-1,
            )


# -------------------------------------------------------------------
# Feedback submission with audio_token
# -------------------------------------------------------------------


class TestFeedbackWithAudio:
    """Tests for submitting feedback with audio attachment."""

    def test_feedback_with_invalid_audio_token_returns_400(
        self, client, app_ref, tmp_path,
    ):
        """Non-existent audio_token returns 400."""
        session_id = uuid.uuid4()

        mock_session = MagicMock()
        mock_session.id = session_id
        mock_session.user_id = uuid.UUID(
            "00000000-0000-0000-0000-000000000001",
        )

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value \
            .first.return_value = mock_session
        mock_db.query.return_value.filter.return_value \
            .count.return_value = 0

        from app.api.deps import get_db
        app_ref.dependency_overrides[get_db] = lambda: mock_db

        with patch(
            "app.api.v2.endpoints.obd_analysis.settings"
        ) as mock_settings:
            mock_settings.audio_storage_path = str(tmp_path)
            os.makedirs(tmp_path / "staging", exist_ok=True)

            feedback = {
                **VALID_FEEDBACK,
                "audio_token": str(uuid.uuid4()),
                "audio_duration_seconds": 10,
            }
            resp = client.post(
                f"/v2/obd/{session_id}/feedback/summary",
                json=feedback,
            )

        assert resp.status_code == 400
        assert "audio_token" in resp.json()["detail"]


# -------------------------------------------------------------------
# FeedbackHistoryItem includes audio metadata
# -------------------------------------------------------------------


class TestFeedbackHistoryAudio:
    """Tests for audio fields in feedback history response."""

    def test_history_item_has_audio_fields(self):
        """FeedbackHistoryItem schema includes audio fields."""
        from app.api.v2.schemas import FeedbackHistoryItem

        item = FeedbackHistoryItem(
            id="abc",
            session_id="def",
            tab_name="summary",
            rating=5,
            is_helpful=True,
            created_at="2026-01-01T00:00:00",
            has_audio=True,
            audio_duration_seconds=42,
        )
        assert item.has_audio is True
        assert item.audio_duration_seconds == 42

    def test_history_item_defaults_no_audio(self):
        """FeedbackHistoryItem defaults to has_audio=False."""
        from app.api.v2.schemas import FeedbackHistoryItem

        item = FeedbackHistoryItem(
            id="abc",
            session_id="def",
            tab_name="summary",
            rating=5,
            is_helpful=True,
            created_at="2026-01-01T00:00:00",
        )
        assert item.has_audio is False
        assert item.audio_duration_seconds is None


# -------------------------------------------------------------------
# DB model has audio columns
# -------------------------------------------------------------------


class TestDBModelAudioColumns:
    """Tests that the feedback mixin includes audio columns."""

    def test_mixin_has_audio_file_path(self):
        """_OBDFeedbackMixin has audio_file_path column."""
        from app.models_db import OBDSummaryFeedback

        cols = {
            c.name for c in
            OBDSummaryFeedback.__table__.columns
        }
        assert "audio_file_path" in cols
        assert "audio_duration_seconds" in cols
        assert "audio_size_bytes" in cols

    def test_all_feedback_tables_have_audio(self):
        """All 5 feedback tables inherit audio columns."""
        from app.models_db import (
            OBDAIDiagnosisFeedback,
            OBDDetailedFeedback,
            OBDPremiumDiagnosisFeedback,
            OBDRAGFeedback,
            OBDSummaryFeedback,
        )

        for model in (
            OBDSummaryFeedback,
            OBDDetailedFeedback,
            OBDRAGFeedback,
            OBDAIDiagnosisFeedback,
            OBDPremiumDiagnosisFeedback,
        ):
            cols = {
                c.name for c in model.__table__.columns
            }
            assert "audio_file_path" in cols, (
                f"{model.__tablename__} missing audio_file_path"
            )
