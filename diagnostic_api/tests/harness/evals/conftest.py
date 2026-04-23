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
            ``"v1/mws150a.jsonl"``.

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

    Returns:
        A ``MagicMock`` shaped like the ``AsyncOpenAI`` client
        surface used by the judge.
    """
    canned = json.dumps({
        "section_match": 1,
        "fact_recall": 1.0,
        "hallucination": 0,
        "citation_present": 1,
        "trajectory_ok": 1,
        "overall": 1.0,
        "reasoning": (
            "[mock-judge] plumbing verification — no real "
            "grading performed."
        ),
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
