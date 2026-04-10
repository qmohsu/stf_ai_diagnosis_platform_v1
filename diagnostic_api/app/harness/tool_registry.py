"""Tool registry with dispatch map for harness diagnostic tools.

Provides a universal ``execute(name, input) -> str`` interface that
the agent loop uses to call any registered tool.  Adding a tool
requires one ``register()`` call — zero changes to the loop.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List

logger = logging.getLogger(__name__)


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

    async def execute(
        self,
        name: str,
        input_data: Dict[str, Any],
    ) -> str:
        """Dispatch a tool call by name.

        Catches all handler exceptions and returns an error string
        so the agent loop never crashes on a tool failure.

        Args:
            name: Registered tool name.
            input_data: Tool input dict (validated by caller or
                the handler itself).

        Returns:
            Handler result string, or an error description string
            if the tool is unknown or the handler raises.
        """
        tool = self._tools.get(name)
        if tool is None:
            msg = (
                f"Error: unknown tool '{name}'. "
                f"Available: {sorted(self._tools)}"
            )
            logger.warning(msg)
            return msg

        try:
            result = await tool.handler(input_data)
            return result
        except Exception as exc:
            msg = (
                f"Error executing tool '{name}': "
                f"{type(exc).__name__}: {exc}"
            )
            logger.error(msg, exc_info=exc)
            return msg

    @property
    def schemas(self) -> List[Dict[str, Any]]:
        """Return tool definitions in OpenAI function-calling format.

        Returns:
            List of dicts, each with ``type`` and ``function`` keys
            matching the OpenAI tool-calling specification.
        """
        return [
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
