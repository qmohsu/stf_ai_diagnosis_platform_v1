"""Pytest fixtures and helpers for the manual-agent eval suite.

Provides:
  - ``load_golden()``: parse a ``golden/{version}/{manual}.jsonl``
    file into a list of ``GoldenEntry`` objects.
  - ``eval_report``: session-scoped fixture that accumulates per-
    test records and writes a JSON artifact to ``reports/`` at
    session teardown.

The ``--run-eval`` CLI flag and ``eval`` marker are registered in
the root ``tests/conftest.py`` so that plain ``pytest`` runs skip
the (slow, costly) eval suite by default.

Author: Li-Ta Hsu
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.harness.evals.schemas import (
    GoldenEntry,
    Grade,
    ManualAgentResult,
)

# Directory containing this file — golden/ and reports/ are
# resolved relative to it.
_EVAL_DIR = Path(__file__).resolve().parent
_GOLDEN_DIR = _EVAL_DIR / "golden"
_REPORTS_DIR = _EVAL_DIR / "reports"


# ── Golden loader ─────────────────────────────────────────────────


def load_golden(rel_path: str) -> List[GoldenEntry]:
    """Parse a golden JSONL file into validated ``GoldenEntry`` list.

    Args:
        rel_path: Path relative to the ``golden/`` directory, e.g.
            ``"v2/locked/mws150a.jsonl"`` (canonical eval set per
            HARNESS-20) or ``"v2/mws150a.jsonl"`` (candidate set
            used by ad-hoc tools).

    Returns:
        List of ``GoldenEntry`` objects in file order.

    Raises:
        FileNotFoundError: If the golden file does not exist.
        pydantic.ValidationError: If any line fails schema
            validation.
    """
    path = _GOLDEN_DIR / rel_path
    entries: List[GoldenEntry] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line_num, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entries.append(
                    GoldenEntry.model_validate_json(stripped),
                )
            except Exception as exc:
                raise ValueError(
                    f"Golden file {rel_path} line {line_num} "
                    f"failed validation: {exc}",
                ) from exc
    return entries


# ── Eval report accumulator ──────────────────────────────────────


@dataclass
class EvalReport:
    """In-memory accumulator for one eval run.

    Each call to ``record()`` captures one (entry, result, grade)
    triple.  At session teardown, ``write()`` serialises the
    accumulated records to a timestamped JSON file under
    ``reports/``.

    Attributes:
        started_at: Unix timestamp when the report was created.
        records: Accumulated eval triples.
    """

    started_at: float = field(default_factory=time.time)
    records: List[Dict[str, Any]] = field(default_factory=list)

    def record(
        self,
        entry: GoldenEntry,
        result: ManualAgentResult,
        grade: Grade,
    ) -> None:
        """Append one graded run to the report."""
        self.records.append({
            "entry": entry.model_dump(),
            "result": result.model_dump(),
            "grade": grade.model_dump(),
        })

    def write(self) -> Path:
        """Serialise the report to ``reports/eval_{timestamp}.json``.

        Returns:
            The absolute path written.
        """
        _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        ts = int(self.started_at)
        out_path = _REPORTS_DIR / f"eval_{ts}.json"
        payload = {
            "started_at": self.started_at,
            "ended_at": time.time(),
            "count": len(self.records),
            "records": self.records,
        }
        with open(out_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, default=str)
        return out_path


def _build_mock_judge_client() -> Any:
    """Build a fake ``AsyncOpenAI`` client returning a perfect Grade.

    Used when ``--mock-judge`` is passed so engineers can exercise
    the eval plumbing without consuming OpenRouter credits.  Every
    request returns the same canned JSON payload — a
    plumbing-verification aid, NOT a meaningful score.

    Payload shape matches what ``judge._parse_judge_payload``
    expects in the post-HARNESS-15 / HARNESS-21 era:
    ``answer_quality`` + ``reasoning`` + ``pitfall_violations``
    (empty list means "no violations" — the mock judge is
    deliberately oblivious to authoring quality).

    Returns:
        A ``MagicMock`` shaped like the ``AsyncOpenAI`` client
        surface used by the judge.
    """
    canned = json.dumps({
        "answer_quality": 1.0,
        "reasoning": (
            "[mock-judge] plumbing verification — no real "
            "grading performed."
        ),
        "pitfall_violations": [],
    })

    msg = MagicMock()
    msg.content = canned
    choice = MagicMock()
    choice.message = msg
    completion = MagicMock()
    completion.choices = [choice]

    client = MagicMock()
    client.chat = MagicMock()
    client.chat.completions = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=completion,
    )
    return client


@pytest.fixture
def judge_client(request: pytest.FixtureRequest) -> Optional[Any]:
    """Provide a judge OpenAI client (real or mocked).

    Returns ``None`` when no flag is passed so ``judge_result``
    builds its default real client from ``settings``.  Returns a
    fake client when ``--mock-judge`` is set, letting plumbing
    runs complete without API access.

    Args:
        request: Injected by pytest; used to read CLI options.

    Returns:
        ``None`` for a real client, else a fake client object.
    """
    if request.config.getoption("--mock-judge"):
        return _build_mock_judge_client()
    return None


def _build_mock_agent_deps() -> Any:
    """Build a ``ManualAgentDeps`` whose LLM returns a canned answer.

    Used when ``--mock-agent`` is passed.  The fake client replies
    with a valid final-JSON payload on the first call, so the
    agent loop terminates immediately with ``stopped_reason =
    "complete"`` and a non-empty summary.  Exercises the full
    parsing + ``ManualAgentResult`` construction path without
    requiring a running Ollama instance.

    Returns:
        A ``ManualAgentDeps`` instance ready to pass to
        ``run_manual_agent`` via the ``deps`` kwarg.
    """
    # Import lazily so conftest stays importable even if one of
    # these modules has an issue (e.g., during very early setup).
    from app.harness.deps import LLMResponse
    from app.harness_agents.manual_agent import (
        ManualAgentConfig,
        ManualAgentDeps,
        create_manual_agent_registry,
    )

    canned = json.dumps({
        "summary": (
            "[mock-agent] plumbing response — no real "
            "investigation performed."
        ),
        "citations": [],
    })

    class _CannedLLMClient:
        async def chat(self, **kwargs: Any) -> LLMResponse:
            return LLMResponse(
                content=canned,
                tool_calls=[],
                finish_reason="stop",
            )

    return ManualAgentDeps(
        llm_client=_CannedLLMClient(),  # type: ignore[arg-type]
        tool_registry=create_manual_agent_registry(),
        config=ManualAgentConfig(),
    )


@pytest.fixture
def manual_agent_deps(
    request: pytest.FixtureRequest,
) -> Optional[Any]:
    """Provide manual-agent deps (real or mocked).

    Returns ``None`` when no flag is passed so ``run_manual_agent``
    falls back to its default deps pointing at local Ollama.
    Returns a canned-response deps object when ``--mock-agent`` is
    set, letting plumbing runs complete without a running LLM.

    Args:
        request: Injected by pytest.

    Returns:
        ``None`` for real deps, else a stub ``ManualAgentDeps``.
    """
    if request.config.getoption("--mock-agent"):
        return _build_mock_agent_deps()
    return None


# ── OBD-agent mock deps (HARNESS-21) ─────────────────────────────


def _build_mock_obd_agent_deps() -> Any:
    """Build an ``OBDAgentDeps`` whose LLM returns a canned answer.

    Mirrors ``_build_mock_agent_deps`` but emits an OBD-shaped
    final JSON ({summary, signal_citations, dtc_citations,
    raw_data, limitations}) so the OBD agent loop's
    ``_parse_final_json`` populates the new
    ``obd_signal_citations`` / ``obd_dtc_citations`` fields on
    the resulting ``SystemRunResult``.

    The canned payload deliberately echoes a "perfect" answer for
    the three dummy goldens in ``v1/yamaha_road_test.jsonl`` —
    those are present only to verify the eval plumbing, not to
    measure agent quality.  Real goldens land in PR [2/3].

    Returns:
        ``OBDAgentDeps`` ready to pass to ``run_obd_agent`` via
        the ``deps`` kwarg.
    """
    # Lazy import: keeps conftest importable even when OBD modules
    # are mid-refactor.
    from app.harness.deps import LLMResponse
    from app.harness_agents.obd_agent import (
        OBDAgentConfig,
        OBDAgentDeps,
        create_obd_agent_registry,
    )

    canned = json.dumps({
        "summary": (
            "[mock-obd-agent] plumbing response — peak RPM was "
            "3906; two DTCs stored on the K-Line ECU; no "
            "evidence of misfire."
        ),
        "signal_citations": [
            {
                "signal": "A_KL_RPM",
                "stat": "max",
                "value": 3906.0,
                "units": "rpm",
            },
        ],
        "dtc_citations": [
            {
                "code": "87F11043000000000000CB",
                "status": "stored",
                "ecu": "K-Line",
            },
            {
                "code": "87F11047000000000000CF",
                "status": "pending",
                "ecu": "K-Line",
            },
        ],
        "raw_data": [],
        "limitations": [
            "Yamaha hex DTC codes cannot be decoded without a "
            "manufacturer-specific decoder.",
        ],
    })

    class _CannedLLMClient:
        async def chat(self, **kwargs: Any) -> LLMResponse:
            return LLMResponse(
                content=canned,
                tool_calls=[],
                finish_reason="stop",
            )

    return OBDAgentDeps(
        llm_client=_CannedLLMClient(),  # type: ignore[arg-type]
        tool_registry=create_obd_agent_registry(),
        config=OBDAgentConfig(),
    )


@pytest.fixture
def obd_agent_deps(
    request: pytest.FixtureRequest,
) -> Optional[Any]:
    """Provide OBD-agent deps (real or mocked).

    Mirrors ``manual_agent_deps``: returns ``None`` for the
    real-LLM path (the runner builds default deps lazily); returns
    a canned-response stub when ``--mock-agent`` is set.

    Args:
        request: Injected by pytest.

    Returns:
        ``None`` for real deps, else a stub ``OBDAgentDeps``.
    """
    if request.config.getoption("--mock-agent"):
        return _build_mock_obd_agent_deps()
    return None


# ── Yamaha session bootstrap (HARNESS-21) ────────────────────────


_YAMAHA_FIXTURE_PATH = (
    # 5 .parent's: conftest.py → evals → harness → tests →
    # diagnostic_api → repo root.  PR [1/3]'s code had 4 .parent's
    # (latent bug — masked by pytest.skip), corrected here in
    # HARNESS-21 [2a/4] now that the bootstrap actually reads the
    # file.
    Path(__file__).resolve().parents[4]
    / "obd_agent" / "fixtures"
    / "yamaha_dual_road_test_20260508.csv"
)
"""Repo-relative path to the canonical Yamaha road-test fixture.

Frozen here so all helpers (UUID derivation, bootstrap, tests)
agree on the same source-of-truth file.  Same path used by
``scripts/compute_yamaha_reference.py``.
"""

_YAMAHA_EVAL_USERNAME = "eval-fixture-user"
"""Synthetic User row owning the Yamaha eval session.

Created idempotently on first real-LLM eval run; no login
credentials are needed (this user never authenticates against
``/auth/login`` — it exists purely to satisfy the FK constraint
on ``OBDAnalysisSession.user_id``).
"""


def _yamaha_session_uuid() -> "uuid.UUID":
    """Deterministic UUID5 of the fixture path.

    Stable across machines and runs because ``uuid5`` is a hash
    of the absolute path string.  Same value the eval suite and
    any external probe (e.g. ``scripts/_real_obd_smoke.py``) would
    derive independently.
    """
    import uuid as _uuid_mod
    return _uuid_mod.uuid5(
        _uuid_mod.NAMESPACE_OID, str(_YAMAHA_FIXTURE_PATH),
    )


def _materialise_fixture_in_storage(session_uuid: "uuid.UUID") -> str:
    """Copy the fixture into ``settings.obd_log_storage_path``.

    The OBD tools look up the raw log via ``resolve_log_path``,
    which joins ``settings.obd_log_storage_path`` with the
    ``OBDAnalysisSession.raw_input_file_path`` column.  Our
    committed fixture lives at ``obd_agent/fixtures/...`` —
    nowhere near the storage root.  Copy on first use so the
    production loader path works unchanged.

    Idempotent: if the destination file already exists and its
    SHA-256 matches the fixture, skip the copy.

    Args:
        session_uuid: Used as the per-session subdirectory name.

    Returns:
        Relative path string to store in
        ``OBDAnalysisSession.raw_input_file_path`` — i.e.
        ``"<uuid>/raw_input.csv"``.
    """
    import hashlib
    import shutil
    from app.config import settings

    rel_path = f"{session_uuid}/raw_input.csv"
    dest_dir = Path(settings.obd_log_storage_path) / str(session_uuid)
    dest_file = dest_dir / "raw_input.csv"

    src_bytes = _YAMAHA_FIXTURE_PATH.read_bytes()
    src_sha = hashlib.sha256(src_bytes).hexdigest()

    if dest_file.exists():
        dst_sha = hashlib.sha256(dest_file.read_bytes()).hexdigest()
        if dst_sha == src_sha:
            return rel_path
        # Hash mismatch — fixture changed.  Overwrite.

    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_YAMAHA_FIXTURE_PATH, dest_file)
    return rel_path


def _get_or_create_yamaha_session(
    session_uuid: "uuid.UUID",
) -> None:
    """Idempotently upsert the User + OBDAnalysisSession rows
    backing the Yamaha eval session.

    All writes are committed in a single transaction.  Safe to
    call repeatedly across pytest sessions — uses get-or-create on
    both rows by their natural keys (``username`` for the User,
    ``id`` for the session).

    Raises:
        Any SQLAlchemy/connection error propagates.  The eval test
        will surface it via fixture setup failure.
    """
    import hashlib
    from app.auth.security import get_password_hash
    from app.db.session import SessionLocal
    from app.models_db import OBDAnalysisSession, User

    raw_path = _materialise_fixture_in_storage(session_uuid)
    fixture_bytes = _YAMAHA_FIXTURE_PATH.read_bytes()
    input_text_hash = hashlib.sha256(fixture_bytes).hexdigest()
    input_size_bytes = len(fixture_bytes)

    db = SessionLocal()
    try:
        # Get-or-create User.
        user = (
            db.query(User)
            .filter(User.username == _YAMAHA_EVAL_USERNAME)
            .first()
        )
        if user is None:
            user = User(
                username=_YAMAHA_EVAL_USERNAME,
                hashed_password=get_password_hash(
                    "eval-fixture-no-login",
                ),
                is_active=True,
            )
            db.add(user)
            db.flush()  # populates user.id

        # Get-or-create OBDAnalysisSession by deterministic id.
        session = (
            db.query(OBDAnalysisSession)
            .filter(OBDAnalysisSession.id == session_uuid)
            .first()
        )
        if session is None:
            session = OBDAnalysisSession(
                id=session_uuid,
                user_id=user.id,
                vehicle_id="JYAMA00000XX000001",  # fixture's redacted VIN
                status="COMPLETED",
                input_text_hash=input_text_hash,
                input_size_bytes=input_size_bytes,
                raw_input_file_path=raw_path,
            )
            db.add(session)
        else:
            # If the fixture file changed (hash mismatch with what
            # was stored), refresh the row.  Keeps the eval honest.
            if session.input_text_hash != input_text_hash:
                session.input_text_hash = input_text_hash
                session.input_size_bytes = input_size_bytes
                session.raw_input_file_path = raw_path

        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@pytest.fixture(scope="session")
def yamaha_session_id(
    request: pytest.FixtureRequest,
) -> str:
    """Provide a stable ``OBDAnalysisSession`` UUID for the Yamaha
    road-test fixture.

    Strategy:

    - Compute a deterministic UUID via ``uuid5(NAMESPACE_OID,
      <fixture-path>)`` so the session ID is stable across runs
      and machines.
    - When ``--mock-agent`` is set, return the UUID immediately
      WITHOUT touching the database — the canned LLM client
      doesn't call tools, so the session row is never read.  This
      keeps PR [1/3]'s plumbing verification dependency-free.
    - When ``--mock-agent`` is NOT set (real-LLM path), idempotently
      bootstrap the User + OBDAnalysisSession rows AND copy the
      fixture into ``settings.obd_log_storage_path`` so
      ``resolve_log_path`` resolves correctly.

    Args:
        request: Injected by pytest; used to read CLI options.

    Returns:
        Stable session UUID as a string.
    """
    session_uuid = _yamaha_session_uuid()

    # Mocked path: skip DB.  The canned LLM client never calls the
    # OBD tools, so the session row is never read.
    if request.config.getoption("--mock-agent"):
        return str(session_uuid)

    # Real-LLM path: idempotent bootstrap.  Raises on connection
    # failure (e.g. running outside the diagnostic-api container
    # with no Postgres reachable) — surfaces an actionable error
    # rather than a silent file-not-found later.
    _get_or_create_yamaha_session(session_uuid)
    return str(session_uuid)


@pytest.fixture(scope="session")
def eval_report() -> EvalReport:
    """Session-scoped accumulator that writes JSON on teardown.

    Usage in a test::

        async def test_foo(entry, eval_report):
            result = await run_manual_agent(entry.question)
            grade = await judge_result(entry, result)
            eval_report.record(entry, result, grade)
            assert grade.overall >= 0.7

    At the end of the pytest session, the accumulated records are
    serialised to ``reports/eval_{timestamp}.json``.

    Yields:
        ``EvalReport`` instance.
    """
    report = EvalReport()
    yield report
    if report.records:
        out_path = report.write()
        # Leave a breadcrumb on the terminal so the user can find
        # the report without rummaging through the directory.
        print(
            f"\n[eval_report] wrote {len(report.records)} "
            f"records to {out_path}",
        )
