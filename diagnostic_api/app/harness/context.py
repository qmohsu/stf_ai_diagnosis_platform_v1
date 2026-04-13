"""Context management for the agent loop.

Provides token estimation, per-tool-result truncation, and
conversation auto-compaction to prevent context window overflow.

Two-tier strategy:
  1. **Tier 1** — Truncate individual tool results that exceed
     ``max_tool_result_tokens``.
  2. **Tier 2** — Auto-compact older conversation turns when the
     estimated total exceeds ``compact_threshold``.

Supports both plain-string and multimodal (``List[ContentBlock]``)
tool results.  Image blocks use a fixed token estimate since their
actual token cost depends on the LLM's vision encoder.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple, Union

import structlog

logger = structlog.get_logger(__name__)

# ── Optional tiktoken import ────────────────────────────────────────

try:
    import tiktoken as _tiktoken
    _ENCODER = _tiktoken.get_encoding("cl100k_base")
    _HAS_TIKTOKEN = True
except ImportError:  # pragma: no cover
    _ENCODER = None  # type: ignore[assignment]
    _HAS_TIKTOKEN = False

# Fallback: approximate characters per token for English text.
_CHARS_PER_TOKEN = 4

# Overhead tokens per message (role, separators, structure).
_MSG_OVERHEAD = 4

# Max chars kept from a tool result in the compacted summary.
_SUMMARY_SNIPPET_LEN = 80

# Fixed token estimate per image in multimodal content.
# OpenAI charges ~1000 tokens for low-detail images.
_IMAGE_TOKEN_ESTIMATE = 1000


# ── Token estimation ────────────────────────────────────────────────


def estimate_tokens(text: str) -> int:
    """Estimate the token count of a text string.

    When ``tiktoken`` is installed, uses the ``cl100k_base``
    encoding for accurate counts.  Otherwise falls back to a
    character-based approximation (``len / 4``).

    Args:
        text: The input string.

    Returns:
        Token count (minimum 1).
    """
    if not text:
        return 1
    if _HAS_TIKTOKEN:
        return max(1, len(_ENCODER.encode(text)))
    return max(1, len(text) // _CHARS_PER_TOKEN)


def estimate_content_tokens(
    content: Union[str, List[Dict[str, Any]]],
) -> int:
    """Estimate tokens for plain string or multimodal content.

    For strings, delegates to ``estimate_tokens()``.  For
    multimodal content-block lists, sums text block tokens and
    adds a fixed estimate per image block.

    Args:
        content: Plain string or OpenAI content-block list.

    Returns:
        Estimated token count.
    """
    if isinstance(content, str):
        return estimate_tokens(content)
    total = 0
    for block in content:
        block_type = block.get("type", "")
        if block_type == "text":
            total += estimate_tokens(block.get("text", ""))
        elif block_type == "image_url":
            total += _IMAGE_TOKEN_ESTIMATE
    return max(1, total)


def estimate_messages_tokens(
    messages: List[Dict[str, Any]],
) -> int:
    """Estimate total tokens across a conversation history.

    Counts ``content`` fields for all roles, plus ``arguments``
    strings inside ``tool_calls`` for assistant messages.  Adds
    a small per-message overhead for role and structure tokens.

    Handles both plain-string and multimodal (list) content
    fields in tool-result messages.

    Args:
        messages: OpenAI-format conversation list.

    Returns:
        Estimated total token count.
    """
    total = 0
    for msg in messages:
        total += _MSG_OVERHEAD
        content = msg.get("content")
        if content:
            total += estimate_content_tokens(content)
        for tc in msg.get("tool_calls", []):
            fn = tc.get("function", {})
            args = fn.get("arguments", "")
            if args:
                total += estimate_tokens(args)
    return total


# ── Tier 1: tool-result truncation ──────────────────────────────────


def truncate_tool_result(
    content: Union[str, List[Dict[str, Any]]],
    max_tokens: int,
) -> Union[str, List[Dict[str, Any]]]:
    """Truncate a tool result that exceeds the token budget.

    For plain strings: head+tail strategy preserves both the
    beginning and end.  For multimodal lists: truncates text
    blocks only; image blocks are kept intact (each counts as
    ``_IMAGE_TOKEN_ESTIMATE`` tokens against the budget).

    Args:
        content: Raw tool output (string or content-block list).
        max_tokens: Per-result token budget.

    Returns:
        Original or truncated content in the same format.
    """
    max_tokens = max(1, max_tokens)
    if estimate_content_tokens(content) <= max_tokens:
        return content

    if isinstance(content, str):
        # Use char estimate for the cut boundary.
        max_chars = max_tokens * _CHARS_PER_TOKEN
        head_chars = int(max_chars * 0.75)
        tail_chars = max_chars - head_chars
        head = content[:head_chars]
        tail = (
            content[-tail_chars:] if tail_chars > 0 else ""
        )
        truncated_count = (
            len(content) - head_chars - tail_chars
        )
        marker = (
            f"\n[…truncated {truncated_count} chars "
            f"({len(content)} total)…]\n"
        )
        return head + marker + tail

    # Multimodal: subtract image token cost from budget,
    # then truncate text blocks with remaining budget.
    image_count = sum(
        1 for b in content
        if b.get("type") == "image_url"
    )
    text_budget = max(
        1,
        max_tokens - image_count * _IMAGE_TOKEN_ESTIMATE,
    )
    text_max_chars = text_budget * _CHARS_PER_TOKEN
    remaining = text_max_chars
    result: List[Dict[str, Any]] = []
    for block in content:
        if block.get("type") != "text":
            result.append(block)
            continue
        text = block.get("text", "")
        if remaining <= 0:
            continue
        if len(text) <= remaining:
            result.append(block)
            remaining -= len(text)
        else:
            result.append({
                "type": "text",
                "text": (
                    text[:remaining]
                    + "\n[…truncated…]"
                ),
            })
            remaining = 0
    return result


# ── Tier 2: conversation compaction ─────────────────────────────────


def _identify_iterations(
    messages: List[Dict[str, Any]],
) -> List[List[int]]:
    """Group message indices into iterations.

    An iteration starts with an assistant message that contains
    ``tool_calls`` and includes all subsequent tool-role messages
    until the next assistant message.

    Args:
        messages: Full conversation history.

    Returns:
        List of iterations, where each iteration is a list of
        message indices (assistant + its tool results).
    """
    iterations: List[List[int]] = []
    current: List[int] = []

    for idx, msg in enumerate(messages):
        role = msg.get("role")
        if role == "assistant" and msg.get("tool_calls"):
            if current:
                iterations.append(current)
            current = [idx]
        elif role == "tool" and current:
            current.append(idx)
        elif role == "assistant" and not msg.get("tool_calls"):
            # Final assistant response (no tool_calls) — not
            # part of an iteration.
            if current:
                iterations.append(current)
                current = []

    if current:
        iterations.append(current)

    return iterations


def _summarize_iteration(
    iteration_num: int,
    messages: List[Dict[str, Any]],
    indices: List[int],
) -> str:
    """Build a one-line summary of a single iteration.

    When multiple tools are called in one iteration, each tool's
    name and a short result snippet are included.

    Args:
        iteration_num: 1-based iteration number.
        messages: Full conversation history.
        indices: Message indices belonging to this iteration.

    Returns:
        Summary string like ``"Iter 1: detect_anomalies ->
        [HIGH] RPM..., search_manual -> [0.87] MWS..."``.
    """
    assistant_msg = messages[indices[0]]
    tool_calls = assistant_msg.get("tool_calls", [])

    # Map tool_call_id -> tool name for correlation.
    id_to_name: Dict[str, str] = {}
    for tc in tool_calls:
        fn = tc.get("function", {})
        id_to_name[tc.get("id", "")] = fn.get("name", "unknown")

    # Build per-tool summaries from tool result messages.
    parts: List[str] = []
    for idx in indices[1:]:
        msg = messages[idx]
        tc_id = msg.get("tool_call_id", "")
        name = id_to_name.get(tc_id, "unknown")
        content = msg.get("content", "")
        # Extract text from multimodal content.
        if isinstance(content, list):
            text_parts = []
            image_count = 0
            for block in content:
                if block.get("type") == "text":
                    text_parts.append(
                        block.get("text", ""),
                    )
                elif block.get("type") == "image_url":
                    image_count += 1
            content = " ".join(text_parts)
            if image_count:
                content += (
                    f" [{image_count} image(s)]"
                )
        snippet = content[:_SUMMARY_SNIPPET_LEN]
        if len(content) > _SUMMARY_SNIPPET_LEN:
            snippet += "..."
        if snippet:
            parts.append(f"{name} -> {snippet}")
        else:
            parts.append(name)

    # Fallback: if no tool results, list tool names only.
    if not parts:
        names = [
            fn.get("function", {}).get("name", "unknown")
            for fn in tool_calls
        ]
        parts = names

    return f"- Iter {iteration_num}: {', '.join(parts)}"


def maybe_compact(
    messages: List[Dict[str, Any]],
    threshold: int,
    keep_recent: int = 2,
) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Compact conversation history if it exceeds the threshold.

    Preserves the system prompt, initial user message, and the
    most recent ``keep_recent`` iterations.  Older iterations are
    replaced by a single summary message.

    Args:
        messages: Mutable conversation list (OpenAI format).
        threshold: Estimated token count that triggers compaction.
        keep_recent: Number of recent iterations to keep intact.

    Returns:
        Tuple of ``(new_messages, compact_info)``.  If no
        compaction was needed, ``compact_info`` is ``None``.
    """
    threshold = max(1, threshold)
    keep_recent = max(0, keep_recent)

    before_tokens = estimate_messages_tokens(messages)

    if before_tokens <= threshold:
        return messages, None

    iterations = _identify_iterations(messages)

    # Need at least (keep_recent + 1) iterations to compact.
    if len(iterations) <= keep_recent:
        logger.debug(
            "context_compact_skip",
            reason="not_enough_iterations",
            iterations=len(iterations),
            keep_recent=keep_recent,
        )
        return messages, None

    old_count = len(iterations) - keep_recent
    old_iters = iterations[:old_count]
    recent_iters = iterations[old_count:]

    # Build summary of old iterations.
    summary_lines = ["[Compacted] Prior iterations summary:"]
    for i, indices in enumerate(old_iters, start=1):
        summary_lines.append(
            _summarize_iteration(i, messages, indices),
        )
    summary_text = "\n".join(summary_lines)

    # Assemble new message list.
    new_messages: List[Dict[str, Any]] = []

    # Keep system + user (first 2 messages).
    new_messages.extend(messages[:2])

    # Insert compacted summary as an assistant message.
    new_messages.append(
        {"role": "assistant", "content": summary_text},
    )

    # Keep recent iterations intact.
    for indices in recent_iters:
        for idx in indices:
            new_messages.append(messages[idx])

    after_tokens = estimate_messages_tokens(new_messages)

    logger.info(
        "context_compacted",
        before_tokens=before_tokens,
        after_tokens=after_tokens,
        compacted_iterations=old_count,
        kept_iterations=keep_recent,
    )

    compact_info = {
        "before_tokens": before_tokens,
        "after_tokens": after_tokens,
        "compacted_iterations": old_count,
        "kept_iterations": keep_recent,
        "strategy": "auto_compact",
    }

    return new_messages, compact_info
