"""Tests for multimodal tool result support in the agent loop.

Verifies that the infrastructure changes to tool_registry, loop,
and context correctly handle both plain-string and multimodal
(List[ContentBlock]) tool outputs.
"""

from __future__ import annotations

import pytest

from app.harness.context import (
    _IMAGE_TOKEN_ESTIMATE,
    _SUMMARY_SNIPPET_LEN,
    _summarize_iteration,
    estimate_content_tokens,
    estimate_messages_tokens,
    estimate_tokens,
    truncate_tool_result,
)
from app.harness.loop import (
    _extract_text_for_sse,
    _make_tool_message,
)
from app.harness.tool_registry import (
    ContentBlock,
    ToolResult,
    _truncate,
)


# ── Fixtures ──────────────────────────────────────────────────────


_TEXT_BLOCK = {
    "type": "text",
    "text": "Section 3.2: Fuel System Troubleshooting",
}

_IMAGE_BLOCK = {
    "type": "image_url",
    "image_url": {"url": "data:image/png;base64,AAAA"},
}

_MULTIMODAL_OUTPUT = [
    {"type": "text", "text": "Before image."},
    _IMAGE_BLOCK,
    {"type": "text", "text": "After image."},
]


# ── _make_tool_message ────────────────────────────────────────────


class TestMakeToolMessage:
    """Tests for loop._make_tool_message()."""

    def test_string_content(self) -> None:
        """Plain string output produces string content field."""
        msg = _make_tool_message("tc-1", "hello")
        assert msg["role"] == "tool"
        assert msg["tool_call_id"] == "tc-1"
        assert msg["content"] == "hello"

    def test_multimodal_content(self) -> None:
        """List output is passed through as content field."""
        msg = _make_tool_message("tc-2", _MULTIMODAL_OUTPUT)
        assert msg["role"] == "tool"
        assert msg["tool_call_id"] == "tc-2"
        assert isinstance(msg["content"], list)
        assert len(msg["content"]) == 3
        assert msg["content"][1]["type"] == "image_url"


# ── _extract_text_for_sse ─────────────────────────────────────────


class TestExtractTextForSSE:
    """Tests for loop._extract_text_for_sse()."""

    def test_string_passthrough(self) -> None:
        """Plain string is returned as-is."""
        assert _extract_text_for_sse("hello") == "hello"

    def test_multimodal_strips_images(self) -> None:
        """Images are replaced with [image] markers."""
        result = _extract_text_for_sse(_MULTIMODAL_OUTPUT)
        assert "[image]" in result
        assert "Before image." in result
        assert "After image." in result
        assert "base64" not in result

    def test_empty_list(self) -> None:
        """Empty list returns empty string."""
        assert _extract_text_for_sse([]) == ""


# ── _truncate (tool_registry) ────────────────────────────────────


class TestRegistryTruncate:
    """Tests for tool_registry._truncate()."""

    def test_string_under_limit(self) -> None:
        """String under limit returned unchanged."""
        assert _truncate("short", 100) == "short"

    def test_string_over_limit(self) -> None:
        """String over limit is truncated with marker."""
        result = _truncate("a" * 200, 50)
        assert len(result) < 200
        assert "truncated" in result

    def test_multimodal_under_limit(self) -> None:
        """Multimodal list under limit returned unchanged."""
        blocks = [
            {"type": "text", "text": "short"},
            _IMAGE_BLOCK,
        ]
        result = _truncate(blocks, 1000)
        assert result == blocks

    def test_multimodal_truncates_text_keeps_images(
        self,
    ) -> None:
        """Multimodal truncation keeps images, cuts text."""
        blocks = [
            {"type": "text", "text": "x" * 500},
            _IMAGE_BLOCK,
            {"type": "text", "text": "y" * 500},
        ]
        result = _truncate(blocks, 100)
        assert isinstance(result, list)
        # Image block must survive.
        image_blocks = [
            b for b in result
            if b.get("type") == "image_url"
        ]
        assert len(image_blocks) == 1
        # Text must be truncated.
        text_blocks = [
            b for b in result
            if b.get("type") == "text"
        ]
        total_text = sum(
            len(b["text"]) for b in text_blocks
        )
        assert total_text < 1000


# ── truncate_tool_result (context) ────────────────────────────────


class TestTruncateToolResult:
    """Tests for context.truncate_tool_result()."""

    def test_string_under_budget(self) -> None:
        """String within budget returned unchanged."""
        result = truncate_tool_result("hello", 100)
        assert result == "hello"

    def test_string_over_budget(self) -> None:
        """String over budget uses head+tail strategy."""
        big = "x" * 10_000
        result = truncate_tool_result(big, 50)
        assert isinstance(result, str)
        assert "truncated" in result
        assert len(result) < len(big)

    def test_multimodal_under_budget(self) -> None:
        """Multimodal content within budget unchanged."""
        blocks = [
            {"type": "text", "text": "short"},
            _IMAGE_BLOCK,
        ]
        result = truncate_tool_result(blocks, 5000)
        assert result == blocks

    def test_multimodal_over_budget(self) -> None:
        """Multimodal truncation preserves images."""
        blocks = [
            {"type": "text", "text": "x" * 40_000},
            _IMAGE_BLOCK,
            {"type": "text", "text": "y" * 40_000},
        ]
        result = truncate_tool_result(blocks, 100)
        assert isinstance(result, list)
        image_blocks = [
            b for b in result
            if b.get("type") == "image_url"
        ]
        assert len(image_blocks) == 1


# ── estimate_content_tokens ───────────────────────────────────────


class TestEstimateContentTokens:
    """Tests for context.estimate_content_tokens()."""

    def test_string(self) -> None:
        """String delegates to estimate_tokens."""
        assert estimate_content_tokens("hello") == (
            estimate_tokens("hello")
        )

    def test_multimodal_text_only(self) -> None:
        """Text-only list counted same as concatenated text."""
        blocks = [
            {"type": "text", "text": "hello"},
            {"type": "text", "text": "world"},
        ]
        result = estimate_content_tokens(blocks)
        assert result > 0
        # No image blocks so should be close to pure text.
        assert result < _IMAGE_TOKEN_ESTIMATE

    def test_multimodal_with_image(self) -> None:
        """Image block adds _IMAGE_TOKEN_ESTIMATE to count."""
        blocks = [
            {"type": "text", "text": "short"},
            _IMAGE_BLOCK,
        ]
        result = estimate_content_tokens(blocks)
        assert result >= _IMAGE_TOKEN_ESTIMATE

    def test_empty_list(self) -> None:
        """Empty list returns minimum 1."""
        assert estimate_content_tokens([]) == 1


# ── estimate_messages_tokens with multimodal ──────────────────────


class TestEstimateMessagesTokensMultimodal:
    """Tests for multimodal content in estimate_messages_tokens."""

    def test_tool_message_with_list_content(self) -> None:
        """Tool message with list content is estimated."""
        messages = [
            {"role": "system", "content": "System prompt"},
            {
                "role": "tool",
                "tool_call_id": "tc-1",
                "content": _MULTIMODAL_OUTPUT,
            },
        ]
        result = estimate_messages_tokens(messages)
        assert result >= _IMAGE_TOKEN_ESTIMATE


# ── _summarize_iteration with multimodal ──────────────────────────


class TestSummarizeIterationMultimodal:
    """Tests for _summarize_iteration with multimodal content."""

    def test_multimodal_tool_result_summarized(self) -> None:
        """Multimodal results are summarized as text + count."""
        messages = [
            # Index 0: assistant with tool_calls
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "tc-1",
                        "type": "function",
                        "function": {
                            "name": "read_manual_section",
                            "arguments": "{}",
                        },
                    },
                ],
            },
            # Index 1: multimodal tool result
            {
                "role": "tool",
                "tool_call_id": "tc-1",
                "content": [
                    {
                        "type": "text",
                        "text": "Fuel System Troubleshooting",
                    },
                    _IMAGE_BLOCK,
                    _IMAGE_BLOCK,
                ],
            },
        ]
        summary = _summarize_iteration(
            1, messages, [0, 1],
        )
        assert "read_manual_section" in summary
        assert "Fuel System" in summary
        assert "2 image(s)" in summary
        # No base64 data in summary.
        assert "AAAA" not in summary

    def test_string_tool_result_unchanged(self) -> None:
        """Plain string results still work normally."""
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "tc-1",
                        "type": "function",
                        "function": {
                            "name": "search_manual",
                            "arguments": "{}",
                        },
                    },
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "tc-1",
                "content": "[0.87] MWS150-A#3.2 -- Fuel...",
            },
        ]
        summary = _summarize_iteration(
            1, messages, [0, 1],
        )
        assert "search_manual" in summary
        assert "0.87" in summary


# ── ToolResult dataclass ──────────────────────────────────────────


class TestToolResultMultimodal:
    """Tests for ToolResult with multimodal output."""

    def test_string_output(self) -> None:
        """String output works as before."""
        tr = ToolResult(output="text", duration_ms=1.0)
        assert tr.output == "text"
        assert not tr.is_error

    def test_list_output(self) -> None:
        """List output is accepted."""
        tr = ToolResult(
            output=_MULTIMODAL_OUTPUT,
            duration_ms=2.0,
        )
        assert isinstance(tr.output, list)
        assert len(tr.output) == 3
