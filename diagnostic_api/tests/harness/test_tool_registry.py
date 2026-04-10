"""Tests for the ToolRegistry dispatch and schema assembly."""

import pytest

from app.harness.tool_registry import (
    ToolDefinition,
    ToolRegistry,
)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

async def _echo_handler(input_data: dict) -> str:
    """Trivial handler that echoes the input."""
    return f"echo: {input_data}"


async def _boom_handler(input_data: dict) -> str:
    """Handler that always raises."""
    raise RuntimeError("boom")


def _make_def(
    name: str = "echo",
    handler=None,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"Test tool {name}.",
        input_schema={
            "type": "object",
            "properties": {
                "msg": {"type": "string"},
            },
            "required": ["msg"],
        },
        handler=handler or _echo_handler,
    )


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


class TestRegisterAndExecute:
    """Registration and dispatch basics."""

    @pytest.mark.asyncio
    async def test_register_and_execute(self):
        """Registering a tool and executing it returns str."""
        reg = ToolRegistry()
        reg.register(_make_def("echo"))

        result = await reg.execute("echo", {"msg": "hi"})

        assert isinstance(result, str)
        assert "hi" in result

    @pytest.mark.asyncio
    async def test_execute_returns_str(self):
        """Handler output is always str type."""
        reg = ToolRegistry()
        reg.register(_make_def("echo"))

        result = await reg.execute("echo", {})
        assert isinstance(result, str)


class TestErrorHandling:
    """Error paths return strings, never raise."""

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error_string(self):
        """Calling a non-existent tool returns an error string."""
        reg = ToolRegistry()

        result = await reg.execute("no_such_tool", {})

        assert isinstance(result, str)
        assert "unknown tool" in result.lower()
        assert "no_such_tool" in result

    @pytest.mark.asyncio
    async def test_handler_exception_returns_error_string(
        self,
    ):
        """A raising handler produces an error string."""
        reg = ToolRegistry()
        reg.register(_make_def("boom", handler=_boom_handler))

        result = await reg.execute("boom", {})

        assert isinstance(result, str)
        assert "Error" in result
        assert "boom" in result

    def test_duplicate_registration_raises(self):
        """Registering the same name twice raises ValueError."""
        reg = ToolRegistry()
        reg.register(_make_def("dup"))

        with pytest.raises(ValueError, match="already registered"):
            reg.register(_make_def("dup"))


class TestSchemas:
    """OpenAI function-calling schema generation."""

    def test_schemas_returns_openai_format(self):
        """Each schema entry has type=function and function dict."""
        reg = ToolRegistry()
        reg.register(_make_def("alpha"))
        reg.register(_make_def("beta"))

        schemas = reg.schemas

        assert isinstance(schemas, list)
        assert len(schemas) == 2

        for s in schemas:
            assert s["type"] == "function"
            func = s["function"]
            assert "name" in func
            assert "description" in func
            assert "parameters" in func
            assert func["parameters"]["type"] == "object"

    def test_schemas_empty_registry(self):
        """Empty registry returns empty list."""
        reg = ToolRegistry()
        assert reg.schemas == []

    def test_tool_names(self):
        """tool_names returns sorted list."""
        reg = ToolRegistry()
        reg.register(_make_def("beta"))
        reg.register(_make_def("alpha"))

        assert reg.tool_names == ["alpha", "beta"]
