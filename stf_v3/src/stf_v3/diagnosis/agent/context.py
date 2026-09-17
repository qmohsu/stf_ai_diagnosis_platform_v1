"""Context budget: token estimate, tool-result truncation, history compaction.

Ported from V2 ``harness/context.py`` minus the import-time tiktoken
download (memory: it blocked offline tests).  Estimation is a local
heuristic that treats every CJK character as one token (FM-44: the
TRICITY155 manual is Chinese; ``len/4`` under-counted it three-fold).

Compaction is a Pydantic AI history processor: it changes ONLY what is
sent to the model for that request (the run's stored history is
untouched, FM-2) and folds whole request/response pairs so the result is
a valid history — no orphan tool returns (FM-45).

Author: Xiangzhu Yan
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence

import structlog
from pydantic_ai import RunContext
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    SystemPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

logger = structlog.get_logger(__name__)

_CHARS_PER_TOKEN = 4
_MSG_OVERHEAD = 4
_SUMMARY_SNIPPET_LEN = 80
_IMAGE_TOKEN_ESTIMATE = 1000


# ── Token estimation ─────────────────────────────────────────────


def _is_cjk(ch: str) -> bool:
    return unicodedata.east_asian_width(ch) in ("W", "F")


def estimate_tokens(text: str) -> int:
    """Estimate tokens: CJK / wide chars count 1 each, the rest ``len/4``.

    Args:
        text: Any string.

    Returns:
        Token estimate (minimum 1).
    """
    if not text:
        return 1
    wide = sum(1 for ch in text if _is_cjk(ch))
    narrow = len(text) - wide
    return max(1, wide + narrow // _CHARS_PER_TOKEN)


def _part_text(part: Any) -> str:
    content = getattr(part, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, (list, tuple)):
        out = []
        for item in content:
            if isinstance(item, str):
                out.append(item)
            else:
                out.append("<image>")
        return "\n".join(out)
    if content is None:
        args = getattr(part, "args", None)
        return str(args) if args else ""
    return str(content)


def estimate_message_tokens(message: ModelMessage) -> int:
    """Estimate one message (all parts + overhead)."""
    total = _MSG_OVERHEAD
    for part in message.parts:
        if isinstance(part, ToolCallPart):
            total += estimate_tokens(part.tool_name) + estimate_tokens(
                part.args if isinstance(part.args, str) else str(part.args or "")
            )
            continue
        text = _part_text(part)
        total += estimate_tokens(text) + _IMAGE_TOKEN_ESTIMATE * text.count("<image>")
    return total


def estimate_history_tokens(messages: Sequence[ModelMessage]) -> int:
    """Estimate a whole history."""
    return sum(estimate_message_tokens(m) for m in messages)


# ── Tier 1: tool-result truncation ───────────────────────────────


def truncate_text(content: str, max_tokens: int) -> str:
    """Head + tail truncation with a marker when over budget.

    Args:
        content: Tool output.
        max_tokens: Budget in estimated tokens.

    Returns:
        The original text, or head (75 %) + marker + tail (25 %).
    """
    max_tokens = max(1, max_tokens)
    if estimate_tokens(content) <= max_tokens:
        return content
    # Scale the character budget by the text's own chars-per-token ratio.
    ratio = len(content) / estimate_tokens(content)
    max_chars = max(1, int(max_tokens * ratio))
    head_chars = int(max_chars * 0.75)
    tail_chars = max_chars - head_chars
    head = content[:head_chars]
    tail = content[-tail_chars:] if tail_chars > 0 else ""
    dropped = len(content) - head_chars - tail_chars
    return f"{head}\n[…truncated {dropped} chars ({len(content)} total)…]\n{tail}"


# ── Tier 2: history compaction (Pydantic AI history processor) ───


@dataclass
class CompactionRecord:
    """What a compaction did (for the ``context_compact`` event)."""

    before_tokens: int
    after_tokens: int
    compacted_pairs: int
    kept_pairs: int


def _pairs(messages: List[ModelMessage]) -> List[List[int]]:
    """Group indices into [response, following request] iterations.

    The first request (system + user prompt) is never grouped; each
    ``ModelResponse`` starts a pair that includes the ``ModelRequest``
    carrying its tool returns.
    """
    groups: List[List[int]] = []
    current: List[int] = []
    for idx, msg in enumerate(messages):
        if isinstance(msg, ModelResponse):
            if current:
                groups.append(current)
            current = [idx]
        elif current:
            current.append(idx)
    if current:
        groups.append(current)
    return groups


def _summarise_pair(num: int, messages: List[ModelMessage], indices: List[int]) -> str:
    resp = messages[indices[0]]
    names: Dict[str, str] = {}
    for part in resp.parts:
        if isinstance(part, ToolCallPart):
            names[part.tool_call_id] = part.tool_name
    parts: List[str] = []
    for idx in indices[1:]:
        for part in messages[idx].parts:
            if isinstance(part, ToolReturnPart):
                text = _part_text(part).replace("\n", " ")
                snippet = text[:_SUMMARY_SNIPPET_LEN] + ("..." if len(text) > _SUMMARY_SNIPPET_LEN else "")
                parts.append(f"{names.get(part.tool_call_id, part.tool_name)} -> {snippet}")
            elif isinstance(part, RetryPromptPart):
                parts.append(f"{part.tool_name or 'tool'} -> (retry requested)")
    if not parts:
        text_parts = [p.content for p in resp.parts if isinstance(p, TextPart)]
        if text_parts:
            t = " ".join(text_parts).replace("\n", " ")
            parts.append("assistant: " + t[:_SUMMARY_SNIPPET_LEN])
        parts.extend(n for n in names.values())
    return f"- Iter {num}: {', '.join(parts) if parts else '(no tool results)'}"


def compact_history(
    messages: List[ModelMessage],
    threshold: int,
    keep_recent: int = 2,
) -> tuple[List[ModelMessage], Optional[CompactionRecord]]:
    """Fold older iterations into one summary when over ``threshold``.

    Keeps the opening request intact and the ``keep_recent`` latest
    response/request pairs; older pairs become one synthetic
    ``ModelRequest(user summary)`` + ``ModelResponse(ack)`` pair, so the
    result is still a well-formed history (FM-45).

    Args:
        messages: Full history (not mutated).
        threshold: Estimated-token trigger.
        keep_recent: Latest pairs to keep verbatim.

    Returns:
        ``(messages_to_send, record)``; ``record`` is None when nothing
        was compacted.
    """
    before = estimate_history_tokens(messages)
    if before <= max(1, threshold):
        return list(messages), None
    groups = _pairs(list(messages))
    if len(groups) <= keep_recent:
        return list(messages), None
    old, recent = groups[: len(groups) - keep_recent], groups[len(groups) - keep_recent:]
    first_group_start = groups[0][0]
    summary_lines = ["[Compacted] Prior iterations summary:"]
    summary_lines += [_summarise_pair(i, list(messages), g) for i, g in enumerate(old, start=1)]
    summary = "\n".join(summary_lines)
    out: List[ModelMessage] = list(messages[:first_group_start])
    out.append(ModelRequest(parts=[UserPromptPart(content=summary)]))
    out.append(ModelResponse(parts=[TextPart(content="Understood — continuing from the summary above.")]))
    for g in recent:
        out.extend(messages[i] for i in g)
    after = estimate_history_tokens(out)
    record = CompactionRecord(before, after, len(old), keep_recent)
    logger.info("agent.context_compacted", before_tokens=before, after_tokens=after,
                compacted_pairs=len(old), kept_pairs=keep_recent)
    return out, record


def is_well_formed(messages: Sequence[ModelMessage]) -> bool:
    """Every tool return references a tool call in the preceding response."""
    open_calls: set = set()
    for msg in messages:
        if isinstance(msg, ModelResponse):
            open_calls = {p.tool_call_id for p in msg.parts if isinstance(p, ToolCallPart)}
        elif isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, (ToolReturnPart, RetryPromptPart)) and part.tool_call_id:
                    if part.tool_call_id not in open_calls:
                        return False
    return True


def make_history_processor(
    threshold: int,
    on_compact: Optional[Callable[[CompactionRecord], None]] = None,
) -> Callable[[RunContext[Any], List[ModelMessage]], List[ModelMessage]]:
    """Build the ``ProcessHistory`` callable for an agent.

    Args:
        threshold: Estimated-token trigger for compaction.
        on_compact: Called with the record when compaction happened
            (the runtime turns it into a ``context_compact`` event).
    """

    def processor(ctx: RunContext[Any], messages: List[ModelMessage]) -> List[ModelMessage]:
        compacted, record = compact_history(messages, threshold)
        if record is not None and on_compact is not None:
            on_compact(record)
        return compacted

    return processor


def last_assistant_text(messages: Sequence[ModelMessage], max_chars: int = 4000) -> str:
    """Last non-empty assistant text (partial reports on budget stops)."""
    for msg in reversed(list(messages)):
        if isinstance(msg, ModelResponse):
            texts = [p.content for p in msg.parts if isinstance(p, TextPart) and p.content.strip()]
            if texts:
                return "\n".join(texts).strip()[:max_chars]
    return ""


__all__ = [
    "CompactionRecord", "compact_history", "estimate_history_tokens",
    "estimate_message_tokens", "estimate_tokens", "is_well_formed",
    "last_assistant_text", "make_history_processor", "truncate_text",
    "SystemPromptPart", "ThinkingPart",
]
