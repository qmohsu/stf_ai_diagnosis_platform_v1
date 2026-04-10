"""Dependency injection container and LLM client abstractions.

Provides ``HarnessDeps`` (the DI container passed into the agent loop),
``HarnessConfig`` (tunable knobs), ``HarnessEvent`` (typed yields), and
a protocol-based ``LLMClient`` so the loop can be tested with a mock
LLM that replays pre-recorded responses.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

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
class HarnessEvent:
    """Typed event yielded by the agent loop.

    Attributes:
        event_type: One of ``tool_call``, ``tool_result``,
            ``done``, or ``error``.
        payload: Event-specific data dict.
    """

    event_type: str
    payload: Dict[str, Any]


# ── Configuration ────────────────────────────────────────────────────


@dataclass(frozen=True)
class HarnessConfig:
    """Tunable knobs for the agent loop.

    Attributes:
        model: OpenRouter model ID (required, no default).
        max_iterations: Hard cap on ReAct iterations.
        max_tokens: Max tokens per LLM call.
        compact_threshold: Character count that triggers context
            compaction (placeholder for HARNESS-04).
        timeout_seconds: Total wall-clock budget for the loop.
        temperature: LLM sampling temperature.
    """

    model: str
    max_iterations: int = 10
    max_tokens: int = 8192
    compact_threshold: int = 60_000
    timeout_seconds: float = 120.0
    temperature: float = 0.3


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
