"""Context management for the agent loop.

Provides token estimation, per-tool-result truncation, and
conversation auto-compaction to prevent context window overflow.

Two-tier strategy:
  1. **Tier 1** — Truncate individual tool results that exceed
     ``max_tool_result_tokens``.
  2. **Tier 2** — Auto-compact older conversation turns when the
     estimated total exceeds ``compact_threshold``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import structlog

logger = structlog.get_logger(__name__)

# Approximate characters per token for English text.
_CHARS_PER_TOKEN = 4

# Overhead tokens per message (role, separators, structure).
_MSG_OVERHEAD = 4

# Max chars kept from a tool result in the compacted summary.
_SUMMARY_SNIPPET_LEN = 80


# ── Token estimation ────────────────────────────────────────────────


def estimate_tokens(text: str) -> int:
    """Estimate the token count of a text string.

    Uses a character-based approximation (``len / 4``) that is
    fast enough to call every iteration.  Accuracy is within
    ~20 %% of a real tokenizer for English-heavy diagnostic text.

    Args:
        text: The input string.

    Returns:
        Estimated token count (minimum 1).
    """
    if not text:
        return 1
    return max(1, len(text) // _CHARS_PER_TOKEN)


def estimate_messages_tokens(
    messages: List[Dict[str, Any]],
) -> int:
    """Estimate total tokens across a conversation history.

    Counts ``content`` fields for all roles, plus ``arguments``
    strings inside ``tool_calls`` for assistant messages.  Adds
    a small per-message overhead for role and structure tokens.

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
            total += estimate_tokens(content)
        for tc in msg.get("tool_calls", []):
            fn = tc.get("function", {})
            args = fn.get("arguments", "")
            if args:
                total += estimate_tokens(args)
    return total


# ── Tier 1: tool-result truncation ──────────────────────────────────


def truncate_tool_result(
    content: str,
    max_tokens: int,
) -> str:
    """Truncate a tool result that exceeds the token budget.

    If the result fits within ``max_tokens``, it is returned
    unchanged.  Otherwise, a head+tail strategy preserves both
    the beginning (most context) and the end (often contains
    summaries, status codes, or conclusions).  The budget is
    split 75 %% head / 25 %% tail.

    Args:
        content: Raw tool output string.
        max_tokens: Per-result token budget.

    Returns:
        Original or truncated string with marker.
    """
    max_tokens = max(1, max_tokens)
    max_chars = max_tokens * _CHARS_PER_TOKEN
    if len(content) <= max_chars:
        return content
    head_chars = int(max_chars * 0.75)
    tail_chars = max_chars - head_chars
    head = content[:head_chars]
    tail = content[-tail_chars:] if tail_chars > 0 else ""
    truncated_count = len(content) - head_chars - tail_chars
    marker = (
        f"\n[…truncated {truncated_count} chars "
        f"({len(content)} total)…]\n"
    )
    return head + marker + tail


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

    Args:
        iteration_num: 1-based iteration number.
        messages: Full conversation history.
        indices: Message indices belonging to this iteration.

    Returns:
        Summary string like ``"Iter 1: detect_anomalies ->
        [HIGH] RPM range_sh..."``.
    """
    assistant_msg = messages[indices[0]]
    tool_names: List[str] = []
    for tc in assistant_msg.get("tool_calls", []):
        fn = tc.get("function", {})
        tool_names.append(fn.get("name", "unknown"))

    snippet = ""
    for idx in indices[1:]:
        content = messages[idx].get("content", "")
        if content:
            snippet = content[:_SUMMARY_SNIPPET_LEN]
            break

    names_str = ", ".join(tool_names)
    line = f"- Iter {iteration_num}: {names_str}"
    if snippet:
        if len(snippet) == _SUMMARY_SNIPPET_LEN:
            snippet += "..."
        line += f" -> {snippet}"
    return line


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
