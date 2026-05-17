"""Unit tests for the delegation tool wrappers (HARNESS-19).

Validates that ``delegate_to_obd_agent`` and
``delegate_to_manual_agent``:

1. Register correctly in the main registry.
2. Are absent from the OBD and manual sub-agent registries
   (recursion guard).
3. Spin up a sub-agent run, await it, and return formatted text.
4. Inject session_id correctly.
"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import patch

import pytest

from app.harness_agents.types import (
    Citation,
    DTCCitation,
    ManualAgentResult,
    OBDAgentResult,
    SignalCitation,
)
from app.harness_tools.delegation_tools import (
    DELEGATE_TO_MANUAL_AGENT_DEF,
    DELEGATE_TO_OBD_AGENT_DEF,
    delegate_to_manual_agent,
    delegate_to_obd_agent,
)


SESSION_ID = "11111111-2222-3333-4444-555555555555"


# ── Recursion guard ──────────────────────────────────────────────


class TestNoRecursion:
    """Sub-agent registries must NOT contain delegation tools."""

    def test_obd_agent_registry_excludes_delegate_obd(self) -> None:
        from app.harness_agents.obd_agent import (
            create_obd_agent_registry,
        )
        registry = create_obd_agent_registry()
        assert (
            "delegate_to_obd_agent" not in registry.tool_names
        )
        assert (
            "delegate_to_manual_agent" not in registry.tool_names
        )

    def test_manual_agent_registry_excludes_delegate_manual(
        self,
    ) -> None:
        from app.harness_agents.manual_agent import (
            create_manual_agent_registry,
        )
        registry = create_manual_agent_registry()
        assert (
            "delegate_to_manual_agent" not in registry.tool_names
        )
        assert (
            "delegate_to_obd_agent" not in registry.tool_names
        )


# ── Main registry membership ─────────────────────────────────────


class TestMainRegistry:
    """Main agent registry contains all 12 tools (HARNESS-19)."""

    def test_default_registry_includes_delegation_tools(
        self,
    ) -> None:
        from app.harness.tool_registry import (
            create_default_registry,
        )
        registry = create_default_registry()
        assert "delegate_to_obd_agent" in registry.tool_names
        assert "delegate_to_manual_agent" in registry.tool_names

    def test_default_registry_includes_six_obd_primitives(
        self,
    ) -> None:
        from app.harness.tool_registry import (
            create_default_registry,
        )
        registry = create_default_registry()
        for tool in (
            "list_signals",
            "read_window",
            "get_signal_stats",
            "find_events",
            "list_dtcs",
            "lookup_dtc",
        ):
            assert tool in registry.tool_names

    def test_default_registry_no_longer_lists_read_obd_data(
        self,
    ) -> None:
        """HARNESS-19: legacy two-mode tool is unregistered."""
        from app.harness.tool_registry import (
            create_default_registry,
        )
        registry = create_default_registry()
        assert "read_obd_data" not in registry.tool_names

    def test_default_registry_includes_four_manual_tools(
        self,
    ) -> None:
        from app.harness.tool_registry import (
            create_default_registry,
        )
        registry = create_default_registry()
        for tool in (
            "list_manuals",
            "get_manual_toc",
            "read_manual_section",
            "search_manual",
        ):
            assert tool in registry.tool_names

    def test_total_tool_count_is_twelve(self) -> None:
        from app.harness.tool_registry import (
            create_default_registry,
        )
        registry = create_default_registry()
        assert len(registry.tool_names) == 12


# ── delegate_to_obd_agent handler ────────────────────────────────


class TestDelegateToOBDAgent:
    """Tests for the OBD delegation handler."""

    @pytest.mark.asyncio
    async def test_returns_formatted_markdown(self) -> None:
        """Handler awaits run_obd_agent and renders the result."""
        captured_args: List[Any] = []

        async def fake_run(inquiry, session_id, deps):
            captured_args.append((inquiry, session_id, deps))
            return OBDAgentResult(
                summary="RPM looked normal.",
                signal_citations=[
                    SignalCitation(
                        signal="RPM",
                        stat="max",
                        value=3906.0,
                        units="rpm",
                    ),
                ],
                dtc_citations=[
                    DTCCitation(
                        code="87F11043...",
                        status="stored",
                        ecu="K-Line",
                    ),
                ],
                limitations=["Yamaha hex undecoded"],
            )

        with patch(
            "app.harness_agents.obd_agent.run_obd_agent",
            new=fake_run,
        ):
            result = await delegate_to_obd_agent({
                "_session_id": SESSION_ID,
                "inquiry": "investigate stored DTCs",
            })

        # Inquiry + session_id were threaded through.
        assert captured_args
        inquiry, sid, _ = captured_args[0]
        assert inquiry == "investigate stored DTCs"
        assert sid == SESSION_ID

        # Output is markdown with structure.
        assert "## OBD sub-agent finding" in result
        assert "RPM looked normal" in result
        assert "Signal citations" in result
        assert "DTC citations" in result
        assert "Limitations" in result
        assert "Yamaha hex undecoded" in result

    @pytest.mark.asyncio
    async def test_handler_surfaces_timeout_as_text(self) -> None:
        """Sub-agent timeout → text, not exception."""
        async def fake_run(inquiry, session_id, deps):
            return OBDAgentResult(
                summary="incomplete",
                limitations=["timed out"],
                stopped_reason="timeout",
            )

        with patch(
            "app.harness_agents.obd_agent.run_obd_agent",
            new=fake_run,
        ):
            result = await delegate_to_obd_agent({
                "_session_id": SESSION_ID,
                "inquiry": "x" * 50,
            })

        assert "TIMED OUT" in result
        assert "incomplete" in result


# ── delegate_to_manual_agent handler ─────────────────────────────


class TestDelegateToManualAgent:
    """Tests for the manual delegation handler."""

    @pytest.mark.asyncio
    async def test_returns_formatted_markdown(self) -> None:
        captured: List[Any] = []

        async def fake_run(inquiry, obd_context, deps):
            captured.append((inquiry, obd_context, deps))
            return ManualAgentResult(
                summary="Section X has the procedure.",
                citations=[
                    Citation(
                        manual_id="MWS150A",
                        slug="3-2-fuel",
                        quote="Check the fuel pressure...",
                    ),
                ],
            )

        with patch(
            "app.harness_agents.manual_agent.run_manual_agent",
            new=fake_run,
        ):
            result = await delegate_to_manual_agent({
                "_session_id": SESSION_ID,
                "inquiry": "look up the fuel pressure procedure",
                "obd_context": "Fuel-trim anomaly observed.",
            })

        # Inquiry + obd_context were threaded through.
        assert captured
        inquiry, obd_ctx, _ = captured[0]
        assert "fuel pressure procedure" in inquiry
        assert obd_ctx == "Fuel-trim anomaly observed."

        assert "## Manual sub-agent finding" in result
        assert "Section X" in result
        assert "MWS150A#3-2-fuel" in result

    @pytest.mark.asyncio
    async def test_obd_context_is_optional(self) -> None:
        captured: List[Any] = []

        async def fake_run(inquiry, obd_context, deps):
            captured.append((inquiry, obd_context, deps))
            return ManualAgentResult(summary="ok")

        with patch(
            "app.harness_agents.manual_agent.run_manual_agent",
            new=fake_run,
        ):
            await delegate_to_manual_agent({
                "_session_id": SESSION_ID,
                "inquiry": "x" * 50,
            })

        assert captured
        _, obd_ctx, _ = captured[0]
        assert obd_ctx is None


# ── ToolDefinition metadata ──────────────────────────────────────


class TestToolDefinitions:
    """Sanity checks on the exported tool defs."""

    def test_delegate_obd_def_name(self) -> None:
        assert DELEGATE_TO_OBD_AGENT_DEF.name == "delegate_to_obd_agent"

    def test_delegate_obd_def_is_read_only(self) -> None:
        assert DELEGATE_TO_OBD_AGENT_DEF.is_read_only is True

    def test_delegate_obd_def_has_large_result_cap(self) -> None:
        """Sub-agent outputs can be long — needs a bigger budget."""
        assert (
            DELEGATE_TO_OBD_AGENT_DEF.max_result_chars >= 50_000
        )

    def test_delegate_manual_def_name(self) -> None:
        assert (
            DELEGATE_TO_MANUAL_AGENT_DEF.name
            == "delegate_to_manual_agent"
        )

    def test_delegate_manual_def_has_input_model(self) -> None:
        assert (
            DELEGATE_TO_MANUAL_AGENT_DEF.input_model is not None
        )
