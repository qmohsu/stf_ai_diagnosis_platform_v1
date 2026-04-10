"""Tool registry with dispatch map for harness diagnostic tools.

Provides a universal ``execute(name, input) -> str`` interface that
the agent loop uses to call any registered tool.  Adding a tool
requires one ``register()`` call — zero changes to the loop.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Type

import structlog
from pydantic import BaseModel

logger = structlog.get_logger(__name__)


def _truncate(text: str, max_chars: int) -> str:
    """Truncate ``text`` to ``max_chars`` with a marker."""
    if len(text) <= max_chars:
        return text
    return (
        text[:max_chars]
        + f"\n[truncated — {len(text)} chars total]"
    )


def _elapsed_ms(t0: float) -> float:
    """Milliseconds since ``t0`` (from ``time.monotonic()``)."""
    return (time.monotonic() - t0) * 1000.0


@dataclass(frozen=True)
class ToolResult:
    """Result of a tool execution including timing metadata.

    Attributes:
        output: The tool's text output (always ``str``).
        duration_ms: Wall-clock execution time in milliseconds.
        is_error: Whether ``output`` is an error message.
    """

    output: str
    duration_ms: float
    is_error: bool = False


@dataclass(frozen=True)
class ToolDefinition:
    """Immutable descriptor for a single diagnostic tool.

    Attributes:
        name: Unique tool identifier (e.g. ``get_pid_statistics``).
        description: Human-readable description used in the OpenAI
            function-calling schema sent to the LLM.
        input_schema: JSON Schema ``parameters`` object describing
            the tool's accepted input.
        handler: Async callable ``(dict) -> str``.  Must always
            return a plain string (privacy invariant).
    """

    name: str
    description: str
    input_schema: dict
    handler: Callable[[Dict[str, Any]], Awaitable[str]]
    input_model: Optional[Type[BaseModel]] = None
    is_read_only: bool = False
    max_result_chars: int = 50_000


class ToolRegistry:
    """Registry of diagnostic tools with dispatch and schema assembly.

    Example::

        registry = ToolRegistry()
        registry.register(ToolDefinition(
            name="echo",
            description="Echo the input.",
            input_schema={"type": "object", "properties": {}},
            handler=lambda d: "echoed",
        ))
        result = await registry.execute("echo", {})
    """

    def __init__(self) -> None:
        self._tools: Dict[str, ToolDefinition] = {}
        self._schemas_cache: Optional[
            List[Dict[str, Any]]
        ] = None

    def register(self, tool: ToolDefinition) -> None:
        """Register a tool.  Raises on duplicate name.

        Args:
            tool: The tool definition to register.

        Raises:
            ValueError: If a tool with the same name is already
                registered.
        """
        if tool.name in self._tools:
            raise ValueError(
                f"Tool '{tool.name}' is already registered."
            )
        self._tools[tool.name] = tool
        self._schemas_cache = None  # Invalidate.

    @staticmethod
    def _validate_input(
        tool: ToolDefinition,
        input_data: Dict[str, Any],
    ) -> str | None:
        """Validate ``input_data`` against the tool's schema.

        When ``input_model`` is set, validates via Pydantic for
        type-safe error messages.  Otherwise falls back to basic
        JSON Schema checks (required fields + types).

        Args:
            tool: The tool whose schema to validate against.
            input_data: Caller-supplied input dict.

        Returns:
            Error message string if validation fails, else None.
        """
        # Prefer Pydantic model validation when available.
        if tool.input_model is not None:
            try:
                tool.input_model(**input_data)
                return None
            except Exception as exc:
                return (
                    f"Validation error for tool "
                    f"'{tool.name}': {exc}"
                )

        # Fallback: basic JSON Schema checks.
        schema = tool.input_schema
        required = schema.get("required", [])
        properties = schema.get("properties", {})

        missing = [
            f for f in required if f not in input_data
        ]
        if missing:
            params = ", ".join(f"`{f}`" for f in missing)
            return (
                f"Validation error for tool '{tool.name}': "
                f"missing required parameter(s): {params}."
            )

        _TYPE_MAP = {
            "string": str,
            "integer": (int,),
            "number": (int, float),
            "boolean": (bool,),
            "array": (list,),
            "object": (dict,),
        }
        errors: List[str] = []
        for key, val in input_data.items():
            prop = properties.get(key)
            if prop is None:
                continue
            expected_type = prop.get("type")
            if expected_type is None:
                continue
            py_types = _TYPE_MAP.get(expected_type)
            if py_types and not isinstance(val, py_types):
                errors.append(
                    f"`{key}` expected {expected_type}, "
                    f"got {type(val).__name__}"
                )
        if errors:
            detail = "; ".join(errors)
            return (
                f"Validation error for tool '{tool.name}': "
                f"{detail}."
            )

        return None

    async def execute(
        self,
        name: str,
        input_data: Dict[str, Any],
    ) -> ToolResult:
        """Dispatch a tool call by name.

        Validates input against the tool's JSON Schema, then calls
        the handler.  Catches all exceptions and returns an error
        ``ToolResult`` so the agent loop never crashes on a tool
        failure.

        Args:
            name: Registered tool name.
            input_data: Tool input dict.

        Returns:
            ``ToolResult`` with output string and duration_ms.
        """
        t0 = time.monotonic()

        tool = self._tools.get(name)
        if tool is None:
            msg = (
                f"Error: unknown tool '{name}'. "
                f"Available: {sorted(self._tools)}"
            )
            logger.warning(msg)
            return ToolResult(
                output=msg,
                duration_ms=_elapsed_ms(t0),
                is_error=True,
            )

        validation_error = self._validate_input(
            tool, input_data,
        )
        if validation_error is not None:
            logger.warning(validation_error)
            return ToolResult(
                output=validation_error,
                duration_ms=_elapsed_ms(t0),
                is_error=True,
            )

        try:
            result = await tool.handler(input_data)
            result = _truncate(result, tool.max_result_chars)
            return ToolResult(
                output=result,
                duration_ms=_elapsed_ms(t0),
            )
        except Exception as exc:
            msg = (
                f"Error executing tool '{name}': "
                f"{type(exc).__name__}: {exc}"
            )
            logger.error(msg, exc_info=exc)
            return ToolResult(
                output=msg,
                duration_ms=_elapsed_ms(t0),
                is_error=True,
            )

    @property
    def schemas(self) -> List[Dict[str, Any]]:
        """Return tool definitions in OpenAI function-calling format.

        Cached after first build; invalidated on ``register()``.

        Returns:
            List of dicts, each with ``type`` and ``function`` keys
            matching the OpenAI tool-calling specification.
        """
        if self._schemas_cache is not None:
            return self._schemas_cache
        self._schemas_cache = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
            for t in self._tools.values()
        ]
        return self._schemas_cache

    @property
    def tool_names(self) -> List[str]:
        """Return sorted list of registered tool names."""
        return sorted(self._tools)


def create_default_registry() -> ToolRegistry:
    """Build a registry pre-loaded with all 7 diagnostic tools.

    Returns:
        A fully populated ``ToolRegistry`` ready for the agent loop.
    """
    from app.harness_tools.history_tools import (
        SEARCH_CASE_HISTORY_DEF,
    )
    from app.harness_tools.obd_tools import (
        DETECT_ANOMALIES_DEF,
        GENERATE_CLUES_DEF,
        GET_PID_STATISTICS_DEF,
        GET_SESSION_CONTEXT_DEF,
    )
    from app.harness_tools.rag_tools import (
        REFINE_SEARCH_DEF,
        SEARCH_MANUAL_DEF,
    )

    registry = ToolRegistry()
    for tool_def in (
        GET_PID_STATISTICS_DEF,
        DETECT_ANOMALIES_DEF,
        GENERATE_CLUES_DEF,
        SEARCH_MANUAL_DEF,
        REFINE_SEARCH_DEF,
        SEARCH_CASE_HISTORY_DEF,
        GET_SESSION_CONTEXT_DEF,
    ):
        registry.register(tool_def)
    return registry
