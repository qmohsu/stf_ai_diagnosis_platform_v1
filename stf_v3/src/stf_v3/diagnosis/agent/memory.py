"""Session memory: Pydantic AI message history ⇄ JSON (``messages`` table).

The whole history round-trips through ``ModelMessagesTypeAdapter``.
Binary parts (manual images) are replaced by a text placeholder before
serialisation so the stored JSON never carries base64 blobs (FM-42);
everything else — thinking parts, tool calls, retries — is preserved
field for field (FM-3).

Author: Xiangzhu Yan
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, List, Sequence

from pydantic_ai import BinaryContent, ModelMessagesTypeAdapter
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart, UserPromptPart
from pydantic_core import to_jsonable_python

IMAGE_PLACEHOLDER = "[binary content omitted from stored history]"


def _strip_content(content: Any) -> Any:
    if isinstance(content, BinaryContent):
        return IMAGE_PLACEHOLDER
    if isinstance(content, (list, tuple)):
        return [IMAGE_PLACEHOLDER if isinstance(c, BinaryContent) else c for c in content]
    return content


def strip_binary(messages: Sequence[ModelMessage]) -> List[ModelMessage]:
    """Copy of ``messages`` with every ``BinaryContent`` replaced by text."""
    out: List[ModelMessage] = []
    for msg in messages:
        if isinstance(msg, ModelRequest):
            parts = []
            for part in msg.parts:
                if isinstance(part, (UserPromptPart, ToolReturnPart)):
                    parts.append(replace(part, content=_strip_content(part.content)))
                else:
                    parts.append(part)
            out.append(replace(msg, parts=parts))
        else:
            out.append(msg)
    return out


def messages_to_jsonable(messages: Sequence[ModelMessage]) -> List[Dict[str, Any]]:
    """JSON-ready list (one dict per message), binary stripped."""
    return to_jsonable_python(strip_binary(messages))


def messages_from_jsonable(data: Sequence[Dict[str, Any]]) -> List[ModelMessage]:
    """Inverse of ``messages_to_jsonable``."""
    return ModelMessagesTypeAdapter.validate_python(list(data))
