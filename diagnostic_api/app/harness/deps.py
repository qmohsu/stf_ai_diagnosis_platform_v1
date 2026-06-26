"""Dependency injection container and LLM client abstractions.

Provides ``HarnessDeps`` (the DI container passed into the agent loop),
``HarnessConfig`` (tunable knobs), ``HarnessEvent`` (typed yields), and
a protocol-based ``LLMClient`` so the loop can be tested with a mock
LLM that replays pre-recorded responses.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import (
    Any,
    AsyncIterator,
    Dict,
    List,
    Literal,
    Optional,
    Protocol,
    Union,
)

import httpx
import structlog
from openai import AsyncOpenAI

from app.harness.tool_registry import ToolRegistry

logger = structlog.get_logger(__name__)


# ── Data models ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class ToolCallInfo:
    """A single tool-call request from the LLM.

    Attributes:
        id: Opaque identifier assigned by the API (used to
            correlate the tool-result message).
        name: Registered tool name.
        arguments: Raw JSON string of tool arguments.
    """

    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class LLMResponse:
    """Normalised LLM response returned by ``LLMClient.chat()``.

    Attributes:
        content: Text content (present when the model stops
            without requesting tool calls).
        tool_calls: Tool-call requests (non-empty when the
            model's finish reason is ``"tool_calls"``).
        finish_reason: ``"stop"`` when the model is done, or
            ``"tool_calls"`` when it wants to invoke tools.
    """

    content: Optional[str]
    tool_calls: List[ToolCallInfo]
    finish_reason: str


@dataclass(frozen=True)
class LLMStreamChunk:
    """An incremental delta yielded by ``LLMClient.chat_stream()``.

    Attributes:
        kind: ``"reasoning"`` for chain-of-thought tokens (the
            model's hidden thinking channel) or ``"content"`` for
            visible answer tokens.
        text: The token fragment.
    """

    kind: Literal["reasoning", "content"]
    text: str


EventType = Literal[
    "session_start",
    "reasoning",
    "token",
    "tool_call",
    "tool_result",
    "hypothesis",
    "context_compact",
    "diagnosis_done",
    "done",
    "error",
]


@dataclass(frozen=True)
class HarnessEvent:
    """Typed event yielded by the agent loop.

    Attributes:
        event_type: One of the ``EventType`` literals
            (e.g. ``session_start``, ``tool_call``,
            ``tool_result``, ``diagnosis_done``, ``done``,
            ``error``).
        payload: Event-specific data dict.
    """

    event_type: EventType
    payload: Dict[str, Any]


# ── Configuration ────────────────────────────────────────────────────


@dataclass(frozen=True)
class HarnessConfig:
    """Tunable knobs for the agent loop.

    Attributes:
        model: OpenRouter model ID (required, no default).
        max_iterations: Hard cap on ReAct iterations.
        max_tokens: Max tokens per LLM call.
        max_tool_result_tokens: Per-tool-result token budget.
            Results exceeding this are truncated with a marker.
        compact_threshold: Estimated token count that triggers
            conversation auto-compaction.
        timeout_seconds: Total wall-clock budget for the loop.
        temperature: LLM sampling temperature.
        locale: Response language code (``"en"``, ``"zh-CN"``,
            ``"zh-TW"``).  Injected into the user message so the
            LLM responds in the requested language.
    """

    model: str
    max_iterations: int = 500
    max_tokens: int = 8192
    max_tool_result_tokens: int = 2000
    compact_threshold: int = 60_000
    timeout_seconds: float = 1200.0
    temperature: float = 0.3
    locale: str = "en"


# ── LLM client protocol + OpenAI adapter ─────────────────────────────


class LLMClient(Protocol):
    """Protocol that any LLM backend must satisfy.

    The agent loop depends only on this interface, so tests can
    inject a mock that replays pre-recorded responses.
    """

    async def chat(
        self,
        *,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> LLMResponse:
        """Send a chat-completions request and return the result.

        Args:
            messages: Conversation history in OpenAI format.
            tools: Tool schemas in OpenAI function-calling format.
            model: Model identifier.
            temperature: Sampling temperature.
            max_tokens: Token budget for the response.

        Returns:
            Normalised ``LLMResponse``.
        """
        ...  # pragma: no cover

    def chat_stream(
        self,
        *,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[Union[LLMStreamChunk, LLMResponse]]:
        """Stream a chat-completions request.

        Yields ``LLMStreamChunk`` objects (reasoning / content
        deltas) as they arrive, then terminates by yielding a
        single ``LLMResponse`` carrying the assembled content,
        tool calls, and finish reason — identical in shape to
        what ``chat()`` returns, so the loop can dispatch tools
        the same way.

        Args:
            messages: Conversation history in OpenAI format.
            tools: Tool schemas in OpenAI function-calling format.
            model: Model identifier.
            temperature: Sampling temperature.
            max_tokens: Token budget for the response.

        Yields:
            ``LLMStreamChunk`` deltas, then a final ``LLMResponse``.
        """
        ...  # pragma: no cover


class OpenAILLMClient:
    """Adapter that wraps ``AsyncOpenAI`` into ``LLMClient``.

    Converts the SDK response objects into the framework's
    ``LLMResponse`` / ``ToolCallInfo`` dataclasses so the agent
    loop never touches vendor-specific types.
    """

    def __init__(self, client: AsyncOpenAI) -> None:
        self._client = client

    async def chat(
        self,
        *,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> LLMResponse:
        """Call OpenAI-compatible chat completions (non-streaming).

        Args:
            messages: Conversation history.
            tools: Tool schemas.
            model: Model identifier.
            temperature: Sampling temperature.
            max_tokens: Token budget.

        Returns:
            Normalised ``LLMResponse``.

        Raises:
            openai.PermissionDeniedError: Model blocked in region.
        """
        response = await self._client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            temperature=temperature,
            max_tokens=max_tokens,
        )

        if not response.choices:
            raise ValueError(
                "LLM returned empty choices array "
                "(possible rate limit or content filter)"
            )

        choice = response.choices[0]
        message = choice.message

        tool_calls: List[ToolCallInfo] = []
        if message.tool_calls:
            for tc in message.tool_calls:
                tool_calls.append(
                    ToolCallInfo(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=tc.function.arguments,
                    )
                )

        finish = choice.finish_reason or "stop"
        if tool_calls and finish != "tool_calls":
            finish = "tool_calls"

        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            finish_reason=finish,
        )

    async def chat_stream(
        self,
        *,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[Union[LLMStreamChunk, LLMResponse]]:
        """Stream chat completions, reassembling a final response.

        Surfaces reasoning (thinking) and content tokens as they
        arrive, accumulates streamed tool-call deltas by index,
        and yields a terminal ``LLMResponse`` matching ``chat()``.

        Args:
            messages: Conversation history.
            tools: Tool schemas.
            model: Model identifier.
            temperature: Sampling temperature.
            max_tokens: Token budget.

        Yields:
            ``LLMStreamChunk`` deltas, then a final ``LLMResponse``.
        """
        stream = await self._client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )

        content_parts: List[str] = []
        # index -> {"id", "name", "args"} accumulator.
        tool_acc: Dict[int, Dict[str, Any]] = {}
        finish: Optional[str] = None

        async for chunk in stream:
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            if choice.finish_reason:
                finish = choice.finish_reason
            delta = choice.delta
            if delta is None:
                continue

            reasoning = _extract_reasoning(delta)
            if reasoning:
                yield LLMStreamChunk("reasoning", reasoning)

            if delta.content:
                content_parts.append(delta.content)
                yield LLMStreamChunk("content", delta.content)

            for tcd in delta.tool_calls or []:
                acc = tool_acc.setdefault(
                    tcd.index,
                    {"id": None, "name": None, "args": ""},
                )
                if tcd.id:
                    acc["id"] = tcd.id
                if tcd.function:
                    if tcd.function.name:
                        acc["name"] = tcd.function.name
                    if tcd.function.arguments:
                        acc["args"] += tcd.function.arguments

        tool_calls: List[ToolCallInfo] = []
        for idx in sorted(tool_acc):
            acc = tool_acc[idx]
            if not acc["name"]:
                continue
            tool_calls.append(
                ToolCallInfo(
                    id=acc["id"] or f"call_{idx}",
                    name=acc["name"],
                    arguments=acc["args"] or "{}",
                )
            )

        finish = finish or "stop"
        if tool_calls and finish != "tool_calls":
            finish = "tool_calls"

        yield LLMResponse(
            content="".join(content_parts) or None,
            tool_calls=tool_calls,
            finish_reason=finish,
        )


def _extract_reasoning(delta: Any) -> Optional[str]:
    """Pull a reasoning/thinking token from a streamed delta.

    Different OpenAI-compatible backends expose the thinking
    channel under different keys.  qwen3 via Ollama uses
    ``reasoning``; some providers use ``reasoning_content``.
    Both may surface as a direct attribute or in ``model_extra``.

    Args:
        delta: The streamed ``choices[0].delta`` object.

    Returns:
        The reasoning token text, or ``None`` if this delta
        carries no thinking content.
    """
    direct = getattr(delta, "reasoning", None) or getattr(
        delta, "reasoning_content", None
    )
    if direct:
        return direct
    extra = getattr(delta, "model_extra", None) or {}
    return extra.get("reasoning") or extra.get("reasoning_content")


# ── Ollama native client (thinking-suppressed) ──────────────────────


def _flatten_tool_content(content: Any) -> str:
    """Reduce a tool-result message's content to plain text.

    The manual tools return either a string or a list of OpenAI
    content blocks (text + ``image_url`` for multimodal sections).
    Ollama's native ``/api/chat`` does not take inline image blocks
    in tool-result messages, and the eval model (``qwen3.5:27b``) is
    text-only anyway — the manual markdown already embeds the
    ``Vision description:`` text for every image — so we keep the
    text parts and drop image blocks.

    Args:
        content: ``str``, ``None``, or a list of content-block dicts.

    Returns:
        The flattened text (empty string for ``None``).
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n".join(parts)
    return str(content)


def _to_ollama_messages(
    messages: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Translate OpenAI-shaped messages to Ollama-native shape.

    The agent loop builds messages in OpenAI format.  Two
    differences matter for ``/api/chat``:

    - Assistant ``tool_calls`` carry ``arguments`` as a JSON
      *string* in OpenAI shape but a JSON *object* natively.
    - Tool-result messages use ``tool_call_id`` in OpenAI shape;
      natively they are matched positionally, so the id is dropped.

    Args:
        messages: Conversation history in OpenAI format.

    Returns:
        A new list of Ollama-native message dicts.
    """
    out: List[Dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        if role == "assistant" and msg.get("tool_calls"):
            native_calls: List[Dict[str, Any]] = []
            for tc in msg["tool_calls"]:
                fn = tc.get("function", {}) or {}
                raw_args = fn.get("arguments", "{}")
                if isinstance(raw_args, str):
                    try:
                        args: Any = json.loads(raw_args or "{}")
                    except json.JSONDecodeError:
                        args = {}
                else:
                    args = raw_args
                native_calls.append({
                    "function": {
                        "name": fn.get("name", ""),
                        "arguments": args,
                    },
                })
            out.append({
                "role": "assistant",
                "content": msg.get("content") or "",
                "tool_calls": native_calls,
            })
        else:
            out.append({
                "role": role,
                "content": _flatten_tool_content(msg.get("content")),
            })
    return out


def _from_ollama_response(data: Dict[str, Any]) -> LLMResponse:
    """Normalise an Ollama ``/api/chat`` response to ``LLMResponse``.

    Converts native tool calls (``arguments`` as an object, no id)
    back into ``ToolCallInfo`` (``arguments`` as a JSON string, with
    a synthesised id) so the agent loop's existing parsing is
    unchanged.

    Args:
        data: Decoded JSON body from ``/api/chat`` (non-streaming).

    Returns:
        Normalised ``LLMResponse``.
    """
    msg = data.get("message", {}) or {}
    tool_calls: List[ToolCallInfo] = []
    for i, tc in enumerate(msg.get("tool_calls") or []):
        fn = tc.get("function", {}) or {}
        args = fn.get("arguments", {})
        if not isinstance(args, str):
            args = json.dumps(args, ensure_ascii=False)
        tool_calls.append(ToolCallInfo(
            id=f"call_{i}",
            name=fn.get("name", ""),
            arguments=args,
        ))
    finish = (
        "tool_calls" if tool_calls
        else (data.get("done_reason") or "stop")
    )
    return LLMResponse(
        content=msg.get("content") or None,
        tool_calls=tool_calls,
        finish_reason=finish,
    )


class OllamaNativeLLMClient:
    """``LLMClient`` backed by Ollama's NATIVE ``/api/chat`` endpoint.

    Unlike ``OpenAILLMClient`` (the OpenAI-compatible ``/v1`` API),
    this client can pass ``"think": false`` — the ONLY mechanism that
    actually suppresses qwen3's hidden reasoning channel.  Both the
    ``/v1`` endpoint and the ``/no_think`` prompt directive are
    *ineffective* (measured ~36 s/call regardless; HARNESS-23 / #144),
    which is what timed out adversarial goldens before the agent could
    navigate AND synthesise inside the wall-clock budget.

    **Scope:** wired only into the manual-agent *eval* deps
    (``tests/harness/evals/runner.py`` + ``scripts/eval_one_golden``).
    Production sub-agent delegation runs on the shared OpenRouter
    client (``delegation_tools._resolve_llm_client``) and is
    unaffected — there is no production latency bug, only an
    eval-measurement artifact from running the local model in
    thinking mode.

    Tool calling and message history are translated to/from
    Ollama-native shape (see ``_to_ollama_messages`` /
    ``_from_ollama_response``).
    """

    def __init__(
        self,
        base_url: str,
        *,
        think: bool = False,
        timeout_seconds: float = 300.0,
    ) -> None:
        """Build the client.

        Args:
            base_url: Ollama root URL (no ``/v1``), e.g.
                ``"http://127.0.0.1:11434"``.
            think: Whether to leave the reasoning channel on.
                Defaults to ``False`` (suppressed) — the whole point
                of this client.
            timeout_seconds: Per-request HTTP timeout.
        """
        self._chat_url = base_url.rstrip("/") + "/api/chat"
        self._think = think
        self._timeout = timeout_seconds

    async def chat(
        self,
        *,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> LLMResponse:
        """Call Ollama ``/api/chat`` (non-streaming) with thinking off.

        Args:
            messages: Conversation history in OpenAI format.
            tools: Tool schemas in OpenAI function-calling format.
            model: Model identifier.
            temperature: Sampling temperature.
            max_tokens: Output token budget (``num_predict``).

        Returns:
            Normalised ``LLMResponse``.
        """
        payload: Dict[str, Any] = {
            "model": model,
            "messages": _to_ollama_messages(messages),
            "think": self._think,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        if tools:
            payload["tools"] = tools
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(self._chat_url, json=payload)
            resp.raise_for_status()
            data = resp.json()
        return _from_ollama_response(data)

    def chat_stream(
        self,
        *,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[Union[LLMStreamChunk, LLMResponse]]:
        """Not implemented — the manual sub-agent never streams."""
        raise NotImplementedError(
            "OllamaNativeLLMClient does not implement chat_stream; "
            "the manual sub-agent loop uses chat() only.",
        )


# ── Dependency container ─────────────────────────────────────────────


@dataclass
class HarnessDeps:
    """Injected dependencies for the agent loop.

    Attributes:
        llm_client: Any object satisfying ``LLMClient`` protocol.
        tool_registry: The tool dispatch map.
        config: Tunable knobs.
    """

    llm_client: LLMClient
    tool_registry: ToolRegistry
    config: HarnessConfig
