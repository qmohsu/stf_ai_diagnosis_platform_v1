"""Tests for the ToolRegistry dispatch and schema assembly."""

import pytest

from app.harness.tool_registry import (
    ToolDefinition,
    ToolRegistry,
    ToolResult,
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
        """Registering a tool and executing it returns ToolResult."""
        reg = ToolRegistry()
        reg.register(_make_def("echo"))

        result = await reg.execute("echo", {"msg": "hi"})

        assert isinstance(result, ToolResult)
        assert isinstance(result.output, str)
        assert "hi" in result.output
        assert result.is_error is False

    @pytest.mark.asyncio
    async def test_duration_ms_is_positive(self):
        """Execution always records a positive duration."""
        reg = ToolRegistry()
        reg.register(_make_def("echo"))

        result = await reg.execute("echo", {"msg": "x"})

        assert result.duration_ms >= 0.0

    @pytest.mark.asyncio
    async def test_execute_returns_str_output(self):
        """Handler output is always str type."""
        reg = ToolRegistry()
        reg.register(_make_def("echo"))

        result = await reg.execute("echo", {"msg": "x"})
        assert isinstance(result.output, str)


class TestErrorHandling:
    """Error paths return ToolResult with is_error=True."""

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        """Calling a non-existent tool returns an error result."""
        reg = ToolRegistry()

        result = await reg.execute("no_such_tool", {})

        assert isinstance(result, ToolResult)
        assert result.is_error is True
        assert "unknown tool" in result.output.lower()
        assert "no_such_tool" in result.output
        assert result.duration_ms >= 0.0

    @pytest.mark.asyncio
    async def test_handler_exception_returns_error(self):
        """A raising handler produces an error ToolResult."""
        reg = ToolRegistry()
        reg.register(_make_def("boom", handler=_boom_handler))

        result = await reg.execute("boom", {"msg": "x"})

        assert isinstance(result, ToolResult)
        assert result.is_error is True
        assert "Error" in result.output
        assert "boom" in result.output

    def test_duplicate_registration_raises(self):
        """Registering the same name twice raises ValueError."""
        reg = ToolRegistry()
        reg.register(_make_def("dup"))

        with pytest.raises(ValueError, match="already registered"):
            reg.register(_make_def("dup"))


class TestInputValidation:
    """Input validation before handler dispatch."""

    @pytest.mark.asyncio
    async def test_missing_required_field_returns_error(self):
        """Missing a required param returns validation error."""
        reg = ToolRegistry()
        reg.register(_make_def("echo"))

        result = await reg.execute("echo", {})

        assert result.is_error is True
        assert "missing required" in result.output.lower()
        assert "`msg`" in result.output

    @pytest.mark.asyncio
    async def test_wrong_type_returns_error(self):
        """Wrong type for a field returns validation error."""
        reg = ToolRegistry()
        reg.register(_make_def("echo"))

        result = await reg.execute("echo", {"msg": 123})

        assert result.is_error is True
        assert "expected string" in result.output.lower()

    @pytest.mark.asyncio
    async def test_valid_input_passes(self):
        """Correct input passes validation and calls handler."""
        reg = ToolRegistry()
        reg.register(_make_def("echo"))

        result = await reg.execute("echo", {"msg": "hello"})

        assert result.is_error is False
        assert "hello" in result.output


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
