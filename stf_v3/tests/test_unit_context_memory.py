"""PROD-08 T-6 / T-7: token estimate, truncation, compaction, memory round-trip.

Author: Xiangzhu Yan
"""

from __future__ import annotations

from pydantic_ai import BinaryContent
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from stf_v3.diagnosis.agent import memory
from stf_v3.diagnosis.agent.context import (
    compact_history,
    estimate_tokens,
    is_well_formed,
    last_assistant_text,
    truncate_text,
)


def test_cjk_text_counts_one_token_per_character() -> None:
    """FM-44: equal-length Chinese and English strings — the Chinese one is
    estimated ~4× higher, not equal."""
    en = "coolant temperature sensor circuit low input " * 10
    zh = "冷卻液溫度感知器電路輸入過低檢查接頭固定狀況" * 20
    assert len(en) >= len(zh) * 0.9
    assert estimate_tokens(zh) > 3 * estimate_tokens(en)


def test_truncate_keeps_head_and_tail_with_marker() -> None:
    """Over-budget text becomes head + marker + tail; short text is untouched."""
    short = "hello"
    assert truncate_text(short, 10) == short
    long = "A" * 4000 + "Z" * 4000
    out = truncate_text(long, 100)
    assert out.startswith("A") and out.endswith("Z") and "[…truncated" in out
    assert len(out) < len(long)


def _history(n_pairs: int) -> list:
    msgs: list = [ModelRequest(parts=[SystemPromptPart(content="sys"), UserPromptPart(content="diagnose")])]
    for i in range(n_pairs):
        msgs.append(ModelResponse(parts=[ToolCallPart(tool_name="list_signals", args={}, tool_call_id=f"c{i}")]))
        msgs.append(ModelRequest(parts=[ToolReturnPart(tool_name="list_signals", content="X" * 400, tool_call_id=f"c{i}")]))
    return msgs


def test_compaction_folds_old_pairs_and_stays_well_formed() -> None:
    """FM-45: the compacted history keeps the opening request and the last
    two pairs verbatim; older pairs become one valid request/response pair;
    no orphan tool returns; the original list is untouched (FM-2)."""
    msgs = _history(6)
    before = list(msgs)
    out, record = compact_history(msgs, threshold=200, keep_recent=2)
    assert record is not None and record.compacted_pairs == 4 and record.kept_pairs == 2
    assert msgs == before
    assert is_well_formed(out) and is_well_formed(msgs)
    assert out[0] is msgs[0]
    assert isinstance(out[1], ModelRequest) and "[Compacted]" in out[1].parts[0].content
    assert out[-1] is msgs[-1] and out[-2] is msgs[-2]
    assert record.after_tokens < record.before_tokens
    same, none = compact_history(msgs, threshold=10**9)
    assert none is None and same == msgs


def test_is_well_formed_detects_orphan_tool_return() -> None:
    """A tool return whose call is not in the preceding response is invalid."""
    msgs = _history(1)
    msgs.append(ModelRequest(parts=[ToolReturnPart(tool_name="x", content="y", tool_call_id="orphan")]))
    assert not is_well_formed(msgs)


def test_memory_round_trip_preserves_parts_and_strips_binary() -> None:
    """FM-3 / FM-42: thinking, tool calls, tool returns and retries survive a
    JSON round trip field for field; image bytes are replaced by a marker."""
    png = BinaryContent(data=b"\x89PNG\r\n", media_type="image/png")
    msgs = [
        ModelRequest(parts=[SystemPromptPart(content="sys"), UserPromptPart(content="q")]),
        ModelResponse(parts=[ThinkingPart(content="hmm"), ToolCallPart(tool_name="read_manual_section",
                                                                        args={"manual_id": "m1", "section": "s"},
                                                                        tool_call_id="c1")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="read_manual_section", content=["text", png], tool_call_id="c1")]),
        ModelResponse(parts=[TextPart(content="final 報告")]),
    ]
    data = memory.messages_to_jsonable(msgs)
    blob = str(data)
    assert "PNG" not in blob and memory.IMAGE_PLACEHOLDER in blob
    back = memory.messages_from_jsonable(data)
    assert len(back) == 4
    assert isinstance(back[1].parts[0], ThinkingPart) and back[1].parts[0].content == "hmm"
    assert back[1].parts[1].tool_call_id == "c1" and back[1].parts[1].args_as_dict()["manual_id"] == "m1"
    assert back[2].parts[0].content == ["text", memory.IMAGE_PLACEHOLDER]
    assert back[3].parts[0].content == "final 報告"
    assert last_assistant_text(back) == "final 報告"
    # the original list still holds the binary (only the copy was stripped)
    assert isinstance(msgs[2].parts[0].content[1], BinaryContent)
