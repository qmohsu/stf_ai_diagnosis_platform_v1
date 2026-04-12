"""Fixture loaders for harness test data.

Provides helpers to load pre-recorded LLM response sequences
from JSON files so integration tests are fully deterministic.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from app.harness.deps import LLMResponse, ToolCallInfo

_FIXTURES_DIR = Path(__file__).parent


def load_llm_responses(
    filename: str,
) -> List[LLMResponse]:
    """Load a JSON fixture into a list of ``LLMResponse``.

    Each JSON entry must have ``content``, ``tool_calls``
    (list of ``{id, name, arguments}``), and ``finish_reason``.

    Args:
        filename: Name of the JSON file inside the fixtures
            directory (e.g. ``"golden_path_responses.json"``).

    Returns:
        Ordered list of ``LLMResponse`` ready for replay.
    """
    path = _FIXTURES_DIR / filename
    with open(path, encoding="utf-8") as fh:
        raw: List[Dict[str, Any]] = json.load(fh)

    responses: List[LLMResponse] = []
    for entry in raw:
        tool_calls = [
            ToolCallInfo(
                id=tc["id"],
                name=tc["name"],
                arguments=tc["arguments"],
            )
            for tc in (entry.get("tool_calls") or [])
        ]
        responses.append(
            LLMResponse(
                content=entry.get("content"),
                tool_calls=tool_calls,
                finish_reason=entry["finish_reason"],
            )
        )
    return responses


def load_fallback_fixture(
    filename: str = "fallback_responses.json",
) -> Dict[str, Any]:
    """Load the fallback test fixture.

    Args:
        filename: JSON file name.

    Returns:
        Dict with ``agent_error`` (str) and
        ``oneshot_tokens`` (list of str).
    """
    path = _FIXTURES_DIR / filename
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)
