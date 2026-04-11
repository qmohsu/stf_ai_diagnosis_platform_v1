"""Tests for context management (``app.harness.context``).

Covers token estimation, per-tool-result truncation, and
conversation auto-compaction.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.harness.context import (
    _CHARS_PER_TOKEN,
    _SUMMARY_SNIPPET_LEN,
    _identify_iterations,
    _summarize_iteration,
    estimate_messages_tokens,
    estimate_tokens,
    maybe_compact,
    truncate_tool_result,
)


# ── Helpers ─────────────────────────────────────────────────────────


def _system_msg(content: str = "System prompt.") -> Dict[str, Any]:
    """Build a system message."""
    return {"role": "system", "content": content}


def _user_msg(content: str = "Diagnose session X.") -> Dict[str, Any]:
    """Build a user message."""
    return {"role": "user", "content": content}


def _assistant_tool_msg(
    *tool_calls: tuple[str, str, str],
) -> Dict[str, Any]:
    """Build an assistant message with tool_calls.

    Args:
        tool_calls: Tuples of (id, name, arguments_json).
    """
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": tc[0],
                "type": "function",
                "function": {
                    "name": tc[1],
                    "arguments": tc[2],
                },
            }
            for tc in tool_calls
        ],
    }


def _tool_msg(
    tool_call_id: str,
    content: str,
) -> Dict[str, Any]:
    """Build a tool result message."""
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": content,
    }


def _assistant_stop_msg(
    content: str = "Final diagnosis.",
) -> Dict[str, Any]:
    """Build an assistant message without tool_calls."""
    return {"role": "assistant", "content": content}


def _build_conversation(
    num_iterations: int,
    tool_result_size: int = 200,
) -> List[Dict[str, Any]]:
    """Build a realistic conversation with N iterations.

    Each iteration has one tool call + one tool result.

    Args:
        num_iterations: Number of tool-call iterations.
        tool_result_size: Character length of each tool result.

    Returns:
        OpenAI-format message list.
    """
    messages: List[Dict[str, Any]] = [
        _system_msg("You are a vehicle diagnostic agent."),
        _user_msg("Diagnose session abc-123."),
    ]
    for i in range(num_iterations):
        tool_name = f"tool_{i}"
        tc_id = f"tc_{i}"
        messages.append(
            _assistant_tool_msg(
                (tc_id, tool_name, f'{{"session_id": "abc"}}'),
            ),
        )
        messages.append(
            _tool_msg(tc_id, "R" * tool_result_size),
        )
    return messages


# ── Tests: token estimation ─────────────────────────────────────────


class TestEstimateTokens:
    """Tests for the character-based token estimator."""

    def test_empty_string_returns_one(self) -> None:
        """Empty string returns minimum of 1 token."""
        assert estimate_tokens("") == 1

    def test_short_string(self) -> None:
        """Short string is estimated as ceil(len/4)."""
        # "hello" = 5 chars -> 5 // 4 = 1
        assert estimate_tokens("hello") == 1

    def test_medium_string(self) -> None:
        """Medium string returns a positive token count."""
        tokens = estimate_tokens("a" * 100)
        assert tokens >= 1

    def test_long_string_proportional(self) -> None:
        """Longer strings produce more tokens."""
        short = estimate_tokens("x" * 100)
        long = estimate_tokens("x" * 4000)
        assert long > short

    def test_within_20_percent_of_tiktoken(self) -> None:
        """Estimation is within 20%% of tiktoken cl100k_base.

        Verifies against five representative diagnostic strings
        covering plain English, DTC codes, numeric data,
        service-manual references, and anomaly reports.
        """
        tiktoken = pytest.importorskip("tiktoken")
        enc = tiktoken.get_encoding("cl100k_base")

        samples = [
            (
                "The RPM anomaly detected at 08:32 suggests "
                "a possible misfire condition in cylinder 3."
            ),
            (
                "[HIGH] RPM range_shift at 12:03:45. "
                "Mean=2847, baseline=1205, z_score=4.2."
            ),
            (
                "Vehicle: V12345, DTCs: P0300 (Random/"
                "Multiple Cylinder Misfire Detected)"
            ),
            (
                "MWS150-A Section 3.2: Check spark plug "
                "gap (0.7-0.8mm). Inspect ignition coil."
            ),
            (
                "No anomalies in COOLANT_TEMP, OIL_PRESSURE"
                ", FUEL_RAIL_PRESSURE, MAF_SENSOR."
            ),
        ]

        for text in samples:
            real = len(enc.encode(text))
            estimated = estimate_tokens(text)
            error_pct = abs(estimated - real) / real * 100
            assert error_pct <= 20, (
                f"estimate_tokens({text!r:.60s}...) = "
                f"{estimated}, tiktoken = {real}, "
                f"error = {error_pct:.1f}%"
            )


class TestEstimateMessagesTokens:
    """Tests for message-list token estimation."""

    def test_two_message_conversation(self) -> None:
        """System + user messages are counted with overhead."""
        msgs = [
            _system_msg("System."),
            _user_msg("User."),
        ]
        tokens = estimate_messages_tokens(msgs)
        # 2 messages * 4 overhead + content tokens
        assert tokens > 8

    def test_includes_tool_call_arguments(self) -> None:
        """Token count includes arguments in tool_calls."""
        msgs = [
            _system_msg(),
            _user_msg(),
            _assistant_tool_msg(
                ("tc1", "detect_anomalies",
                 '{"session_id": "abc"}'),
            ),
        ]
        tokens_with_tc = estimate_messages_tokens(msgs)

        msgs_no_tc = [
            _system_msg(),
            _user_msg(),
            _assistant_stop_msg(),
        ]
        tokens_no_tc = estimate_messages_tokens(msgs_no_tc)

        assert tokens_with_tc > tokens_no_tc

    def test_none_content_handled(self) -> None:
        """Messages with None content don't crash."""
        msgs = [
            {"role": "assistant", "content": None},
        ]
        tokens = estimate_messages_tokens(msgs)
        assert tokens == 4  # overhead only


# ── Tests: tool-result truncation ───────────────────────────────────


class TestTruncateToolResult:
    """Tests for Tier 1 per-tool-result truncation."""

    def test_under_budget_unchanged(self) -> None:
        """Content within budget is returned as-is."""
        content = "Short result."
        result = truncate_tool_result(content, max_tokens=100)
        assert result == content

    def test_exact_budget_unchanged(self) -> None:
        """Content exactly at budget is returned as-is."""
        max_tokens = 10
        max_chars = max_tokens * _CHARS_PER_TOKEN
        content = "x" * max_chars
        result = truncate_tool_result(content, max_tokens)
        assert result == content

    def test_over_budget_truncated(self) -> None:
        """Content exceeding budget is truncated."""
        max_tokens = 10
        max_chars = max_tokens * _CHARS_PER_TOKEN
        content = "x" * (max_chars + 100)
        result = truncate_tool_result(content, max_tokens)
        assert len(result) < len(content)

    def test_truncation_marker_present(self) -> None:
        """Truncated result contains the marker string."""
        content = "y" * 10000
        result = truncate_tool_result(content, max_tokens=5)
        assert "truncated" in result

    def test_marker_includes_total_chars(self) -> None:
        """Truncation marker includes original character count."""
        content = "z" * 12345
        result = truncate_tool_result(content, max_tokens=5)
        assert "12345 total" in result

    def test_zero_budget_floors_to_one(self) -> None:
        """Zero max_tokens is floored to 1 (4 chars)."""
        content = "x" * 100
        result = truncate_tool_result(content, max_tokens=0)
        # Floored to 1 token = 4 chars budget.
        assert "truncated" in result
        assert len(result) < len(content)

    def test_negative_budget_floors_to_one(self) -> None:
        """Negative max_tokens is floored to 1."""
        content = "x" * 100
        result = truncate_tool_result(content, max_tokens=-5)
        assert "truncated" in result

    def test_head_and_tail_preserved(self) -> None:
        """Both head and tail of content are preserved."""
        head = "HEAD_" * 200   # 1000 chars
        middle = "M" * 5000
        tail = "_TAIL" * 200   # 1000 chars
        content = head + middle + tail
        # Budget: 500 tokens = 2000 chars. Content is 7000.
        result = truncate_tool_result(content, max_tokens=500)
        assert result.startswith("HEAD_")
        assert result.endswith("_TAIL")


# ── Tests: iteration identification ─────────────────────────────────


class TestIdentifyIterations:
    """Tests for _identify_iterations helper."""

    def test_single_iteration(self) -> None:
        """One tool call + result is one iteration."""
        msgs = _build_conversation(1)
        iters = _identify_iterations(msgs)
        assert len(iters) == 1
        assert len(iters[0]) == 2  # assistant + tool

    def test_multiple_iterations(self) -> None:
        """N tool-call rounds produce N iterations."""
        msgs = _build_conversation(5)
        iters = _identify_iterations(msgs)
        assert len(iters) == 5

    def test_no_tool_calls(self) -> None:
        """Conversation with no tool calls has zero iterations."""
        msgs = [_system_msg(), _user_msg(), _assistant_stop_msg()]
        iters = _identify_iterations(msgs)
        assert len(iters) == 0


# ── Tests: summarize iteration ──────────────────────────────────────


class TestSummarizeIteration:
    """Tests for _summarize_iteration helper."""

    def test_summary_contains_tool_name(self) -> None:
        """Summary line includes the tool name."""
        msgs = [
            _assistant_tool_msg(
                ("tc1", "detect_anomalies", "{}"),
            ),
            _tool_msg("tc1", "RPM anomaly found"),
        ]
        line = _summarize_iteration(1, msgs, [0, 1])
        assert "detect_anomalies" in line
        assert "Iter 1" in line

    def test_summary_contains_result_snippet(self) -> None:
        """Summary includes a snippet of the tool result."""
        result_text = "HIGH severity RPM range_shift detected"
        msgs = [
            _assistant_tool_msg(("tc1", "tool_a", "{}")),
            _tool_msg("tc1", result_text),
        ]
        line = _summarize_iteration(1, msgs, [0, 1])
        assert "HIGH severity" in line

    def test_long_result_snippet_truncated(self) -> None:
        """Result snippet longer than limit gets ellipsis."""
        long_result = "A" * (_SUMMARY_SNIPPET_LEN + 50)
        msgs = [
            _assistant_tool_msg(("tc1", "tool_a", "{}")),
            _tool_msg("tc1", long_result),
        ]
        line = _summarize_iteration(1, msgs, [0, 1])
        assert "..." in line

    def test_multi_tool_iteration_shows_all(self) -> None:
        """Multiple tool calls in one iteration show all results."""
        msgs = [
            _assistant_tool_msg(
                ("tc1", "detect_anomalies", "{}"),
                ("tc2", "search_manual", "{}"),
            ),
            _tool_msg("tc1", "RPM anomaly found"),
            _tool_msg("tc2", "Manual section 3.2"),
        ]
        line = _summarize_iteration(1, msgs, [0, 1, 2])
        assert "detect_anomalies" in line
        assert "search_manual" in line
        assert "RPM anomaly" in line
        assert "Manual section" in line


# ── Tests: auto-compaction ──────────────────────────────────────────


class TestMaybeCompact:
    """Tests for Tier 2 conversation auto-compaction."""

    def test_under_threshold_no_compaction(self) -> None:
        """No compaction when under the token threshold."""
        msgs = _build_conversation(3, tool_result_size=100)
        result, info = maybe_compact(msgs, threshold=999_999)
        assert info is None
        assert result is msgs  # same object, untouched

    def test_over_threshold_triggers_compaction(self) -> None:
        """Compaction triggers when tokens exceed threshold."""
        msgs = _build_conversation(
            5, tool_result_size=5000,
        )
        result, info = maybe_compact(msgs, threshold=1)
        assert info is not None
        assert info["before_tokens"] > info["after_tokens"]
        assert info["strategy"] == "auto_compact"

    def test_preserves_system_and_user(self) -> None:
        """System prompt and user message always survive."""
        msgs = _build_conversation(
            5, tool_result_size=5000,
        )
        result, info = maybe_compact(msgs, threshold=1)
        assert result[0]["role"] == "system"
        assert result[1]["role"] == "user"
        assert result[0]["content"] == msgs[0]["content"]
        assert result[1]["content"] == msgs[1]["content"]

    def test_preserves_recent_iterations(self) -> None:
        """Last 2 iterations are kept intact."""
        msgs = _build_conversation(
            5, tool_result_size=5000,
        )
        result, info = maybe_compact(
            msgs, threshold=1, keep_recent=2,
        )
        assert info is not None
        assert info["kept_iterations"] == 2
        assert info["compacted_iterations"] == 3

        # Verify the last 2 iterations' tool results are
        # preserved with original content.
        tool_msgs = [
            m for m in result if m.get("role") == "tool"
        ]
        assert len(tool_msgs) == 2
        for tm in tool_msgs:
            # Original content was 'R' * 5000
            assert tm["content"] == "R" * 5000

    def test_compacted_output_shorter(self) -> None:
        """Compacted messages have fewer estimated tokens."""
        msgs = _build_conversation(
            6, tool_result_size=5000,
        )
        result, info = maybe_compact(msgs, threshold=1)
        assert info is not None
        assert info["after_tokens"] < info["before_tokens"]

    def test_summary_message_format(self) -> None:
        """Compacted summary contains tool names and markers."""
        msgs = _build_conversation(
            4, tool_result_size=5000,
        )
        result, info = maybe_compact(
            msgs, threshold=1, keep_recent=2,
        )
        # Third message should be the summary.
        summary = result[2]
        assert summary["role"] == "assistant"
        assert "[Compacted]" in summary["content"]
        assert "tool_0" in summary["content"]
        assert "tool_1" in summary["content"]

    def test_not_enough_iterations_skips(self) -> None:
        """Compaction skipped if <= keep_recent iterations."""
        msgs = _build_conversation(
            2, tool_result_size=50000,
        )
        result, info = maybe_compact(
            msgs, threshold=1, keep_recent=2,
        )
        assert info is None

    def test_compact_info_fields(self) -> None:
        """Compact info dict contains all expected fields."""
        msgs = _build_conversation(
            5, tool_result_size=5000,
        )
        _, info = maybe_compact(msgs, threshold=1)
        assert info is not None
        assert "before_tokens" in info
        assert "after_tokens" in info
        assert "compacted_iterations" in info
        assert "kept_iterations" in info
        assert "strategy" in info

    def test_keep_recent_three(self) -> None:
        """Custom keep_recent=3 preserves 3 iterations."""
        msgs = _build_conversation(
            6, tool_result_size=5000,
        )
        result, info = maybe_compact(
            msgs, threshold=1, keep_recent=3,
        )
        assert info is not None
        assert info["compacted_iterations"] == 3
        assert info["kept_iterations"] == 3

        tool_msgs = [
            m for m in result if m.get("role") == "tool"
        ]
        assert len(tool_msgs) == 3

    def test_negative_threshold_floors_to_one(self) -> None:
        """Negative threshold is floored to 1."""
        msgs = _build_conversation(
            3, tool_result_size=100,
        )
        result, info = maybe_compact(
            msgs, threshold=-10,
        )
        # Even with threshold=1, compaction triggers.
        assert info is not None

    def test_negative_keep_recent_floors_to_zero(self) -> None:
        """Negative keep_recent is floored to 0."""
        msgs = _build_conversation(
            3, tool_result_size=5000,
        )
        result, info = maybe_compact(
            msgs, threshold=1, keep_recent=-1,
        )
        assert info is not None
        # All iterations compacted.
        assert info["compacted_iterations"] == 3
        assert info["kept_iterations"] == 0
