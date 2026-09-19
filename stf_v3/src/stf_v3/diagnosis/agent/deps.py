"""Dependency bundle for one diagnosis run (blueprint §7.2 ``DiagDeps``).

Everything a run needs is loaded ONCE before the agent starts — vehicle
record, log row, the ingested-manual inventory, budgets — so the tools
are pure functions of this object and never open a database session
(FM-50).  Vehicle identity comes from the vehicle record, never from the
log (PROD-08 acceptance ④).

Author: Xiangzhu Yan
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from stf_v3.diagnosis.agent.events import EventSink
from stf_v3.ingest.loader import OBDLogData, load_for_vehicle


def pseudonymise_vin(vin: str) -> str:
    """``V-<8 hex>`` pseudonym (same rule as the V1 corpus redactor)."""
    return "V-" + hashlib.sha256(vin.encode()).hexdigest()[:8].upper()


@dataclass(frozen=True)
class VehicleInfo:
    """The vehicle record fields the agent may see."""

    id: uuid.UUID
    manufacturer: str
    model: str
    vin: str
    plate: Optional[str] = None
    nickname: Optional[str] = None

    def label(self, include_vin: bool) -> str:
        """``"Toyota Hiace (VIN …)"`` — pseudonym when the VIN must not leave."""
        base = f"{self.manufacturer} {self.model}".strip()
        vin = self.vin if include_vin else pseudonymise_vin(self.vin)
        extras = [f"VIN {vin}"]
        if self.plate:
            extras.append(f"plate {self.plate}")
        if self.nickname:
            extras.append(f"nickname {self.nickname}")
        return f"{base} ({', '.join(extras)})"


@dataclass(frozen=True)
class LogInfo:
    """The ``obd_logs`` row the diagnosis is based on."""

    id: uuid.UUID
    vehicle_id: uuid.UUID
    raw_path: str
    format: str
    recorded_start: Optional[datetime] = None
    recorded_end: Optional[datetime] = None
    vin_from_log: Optional[str] = None
    original_filename: Optional[str] = None


@dataclass(frozen=True)
class ManualInfo:
    """One ingested manual (from the ``manuals`` table, status=ingested)."""

    id: str
    manufacturer: str
    vehicle_model: str
    factory_code: Optional[str] = None
    md_file_path: Optional[str] = None
    page_count: Optional[int] = None
    section_count: Optional[int] = None
    language: Optional[str] = None

    @property
    def canonical(self) -> str:
        return f"{self.manufacturer} {self.vehicle_model}".strip()


@dataclass
class Budgets:
    """All the gates, sourced from settings (FM-28)."""

    wall_clock_s: float = 1200.0
    request_limit: int = 80
    tool_calls_limit: int = 120
    total_tokens_limit: int = 600_000
    subagent_wall_clock_s: float = 240.0
    subagent_request_limit: int = 12
    subagent_max_tokens: int = 12_288
    subagent_temperature: float = 0.2
    tool_result_max_tokens: int = 2000
    compact_threshold_tokens: int = 60_000
    llm_max_tokens: int = 8192
    llm_temperature: float = 0.3

    @classmethod
    def from_settings(cls, s: Any, profile: Optional[str] = None) -> "Budgets":
        """Gates from settings; ``None`` fields take the adapter profile's
        default (vLLM / Ollama / cloud differ, PROD-09 FM-8).  An explicit
        setting (env) always wins."""
        from stf_v3.diagnosis.agent.model import profile_defaults, select_profile

        d = profile_defaults(profile or select_profile(s))

        def pick(value: Any, key: str) -> Any:
            return d[key] if value is None else value

        return cls(
            wall_clock_s=pick(s.agent_wall_clock_s, "wall_clock_s"),
            request_limit=pick(s.agent_request_limit, "request_limit"),
            tool_calls_limit=pick(s.agent_tool_calls_limit, "tool_calls_limit"),
            total_tokens_limit=pick(s.agent_total_tokens_limit, "total_tokens_limit"),
            subagent_wall_clock_s=pick(s.subagent_wall_clock_s, "subagent_wall_clock_s"),
            subagent_request_limit=pick(s.subagent_request_limit, "subagent_request_limit"),
            subagent_max_tokens=pick(s.subagent_max_tokens, "subagent_max_tokens"),
            subagent_temperature=pick(s.subagent_temperature, "subagent_temperature"),
            tool_result_max_tokens=s.tool_result_max_tokens,
            compact_threshold_tokens=s.compact_threshold_tokens,
            llm_max_tokens=pick(s.llm_max_tokens, "llm_max_tokens"),
            llm_temperature=pick(s.llm_temperature, "llm_temperature"),
        )


@dataclass
class ToolCallTrace:
    """One executed tool call (kept for citations and sub-agent results)."""

    name: str
    input: Dict[str, Any]
    latency_ms: float
    is_error: bool = False
    tool_call_id: Optional[str] = None
    output_chars: int = 0


@dataclass
class DiagDeps:
    """Dependencies of the main agent run.

    Attributes:
        vehicle: The vehicle record (identity source for prompts).
        log: The log row being diagnosed.
        manuals: Ingested manuals (loaded before the run).
        log_root: ``settings.obd_log_storage_path``.
        manual_root: ``settings.manual_storage_path``.
        budgets: Gates and caps.
        locale: Report language code (``zh-TW`` / ``zh-CN`` / ``en``).
        model_is_local: When False the VIN is pseudonymised in prompts
            (FM-51) — set from ``settings.llm_is_local``.
        images_enabled: Return manual images to the model (FM-42).
        events: Event sink for this run.
        cancel_check: Returns True when the run should stop (FM-55).
        trace: Executed tool calls (main agent + sub-agents).
        memo: (tool, args) → result cache (FM-12).
        durations: tool_call_id → ms, read by the runtime for events.
    """

    vehicle: VehicleInfo
    log: LogInfo
    manuals: List[ManualInfo]
    log_root: Path
    manual_root: Path
    budgets: Budgets = field(default_factory=Budgets)
    locale: str = "zh-TW"
    model_is_local: bool = True
    images_enabled: bool = False
    events: EventSink = field(default_factory=EventSink)
    cancel_check: Optional[Callable[[], bool]] = None
    trace: List[ToolCallTrace] = field(default_factory=list)
    memo: Dict[str, str] = field(default_factory=dict)
    durations: Dict[str, float] = field(default_factory=dict)
    _log_cache: Optional[OBDLogData] = field(default=None, repr=False)

    # ── helpers used by tools ────────────────────────────────────

    def vehicle_label(self) -> str:
        """Vehicle line for prompts; VIN pseudonymised for non-local models."""
        return self.vehicle.label(include_vin=self.model_is_local)

    def load_log(self) -> OBDLogData:
        """Parsed log (cached for the run); ownership checked (FM-4)."""
        if self._log_cache is None:
            self._log_cache = load_for_vehicle(
                self.log_root, self.log.raw_path, self.log.vehicle_id, self.vehicle.id,
            )
        return self._log_cache

    def manual_by_id(self, manual_id: str) -> Optional[ManualInfo]:
        for m in self.manuals:
            if m.id == manual_id:
                return m
        return None


@dataclass
class SubAgentDeps:
    """Dependencies of a sub-agent run: the parent bundle + run state.

    Attributes:
        parent: The main run's ``DiagDeps`` (tools read data through it).
        parent_tool_call_id: The delegation tool call id (events nest under it).
        trace: This sub-run's tool calls.
        raw_sections: Manual sections read (manual sub-agent).
        excerpts: Tool outputs captured as evidence (OBD sub-agent).
        state: Guardrail state (manual sub-agent, FM-40).
    """

    parent: DiagDeps
    parent_tool_call_id: Optional[str]
    trace: List[ToolCallTrace] = field(default_factory=list)
    raw_sections: List[Any] = field(default_factory=list)
    excerpts: List[Tuple[str, str]] = field(default_factory=list)
    state: Any = None
    memo: Dict[str, str] = field(default_factory=dict)


AnyDeps = Any  # DiagDeps | SubAgentDeps (tools accept both)


def core_deps(deps: Any) -> DiagDeps:
    """The main-run bundle behind either deps type."""
    return deps.parent if isinstance(deps, SubAgentDeps) else deps
