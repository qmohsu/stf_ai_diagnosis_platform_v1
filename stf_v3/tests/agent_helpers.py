"""Shared helpers for the PROD-08 agent tests (offline).

Author: Xiangzhu Yan
"""

from __future__ import annotations

import pathlib
import uuid
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage, RunUsage

from stf_v3.diagnosis.agent.deps import Budgets, DiagDeps, LogInfo, ManualInfo, VehicleInfo

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
FAKE_VIN = "JHMGK5830HX202404"
FAKE_VIN_B = "1HGCM82633A123456"

LOG_FILES = {
    "tsv": ("jetson_tsv_ok.tsv", "tsv"),
    "yamaha": ("yamaha_dual.csv", "yamaha"),
    "maxlog": ("obd_maxlog_hiace.csv", "maxlog"),
    "maxlog_no_vin": ("obd_maxlog_no_vin.csv", "maxlog"),
    "maxlog_sample": ("obd_maxlog_sample.csv", "maxlog"),
}


def small_manual(manual_id: str = "m1", **overrides: Any) -> ManualInfo:
    """The ``manual_small.md`` fixture as a Honda Jazz manual (or overridden)."""
    kwargs: Dict[str, Any] = dict(
        id=manual_id, manufacturer="Honda", vehicle_model="Jazz", factory_code="GK5",
        md_file_path="manual_small.md", page_count=12, section_count=6, language="en",
    )
    kwargs.update(overrides)
    return ManualInfo(**kwargs)


def make_deps(
    log: str = "maxlog",
    *,
    manufacturer: str = "Toyota",
    model: str = "Hiace",
    vin: str = FAKE_VIN,
    manuals: Optional[List[ManualInfo]] = None,
    budgets: Optional[Budgets] = None,
    model_is_local: bool = True,
    images_enabled: bool = False,
    manual_root: Optional[pathlib.Path] = None,
    log_root: Optional[pathlib.Path] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    vehicle_id: Optional[uuid.UUID] = None,
    log_vehicle_id: Optional[uuid.UUID] = None,
) -> DiagDeps:
    """A ``DiagDeps`` over the fixture files (no database)."""
    vid = vehicle_id or uuid.uuid4()
    filename, fmt = LOG_FILES[log]
    return DiagDeps(
        vehicle=VehicleInfo(id=vid, manufacturer=manufacturer, model=model, vin=vin, plate="AB 1234"),
        log=LogInfo(id=uuid.uuid4(), vehicle_id=log_vehicle_id or vid, raw_path=filename, format=fmt,
                    original_filename=filename),
        manuals=manuals if manuals is not None else [small_manual()],
        log_root=log_root or FIXTURES,
        manual_root=manual_root or FIXTURES,
        budgets=budgets or Budgets(wall_clock_s=60.0, subagent_wall_clock_s=30.0),
        model_is_local=model_is_local,
        images_enabled=images_enabled,
        cancel_check=cancel_check,
    )


def stub_ctx(deps: Any, tool_call_id: str = "call-1") -> Any:
    """Minimal stand-in for ``RunContext`` (tools use ``deps`` / ``tool_call_id``)."""
    return SimpleNamespace(deps=deps, tool_call_id=tool_call_id, usage=RunUsage(), model=None)


def tool_call(name: str, **args: Any) -> ToolCallPart:
    return ToolCallPart(tool_name=name, args=args)


def response(*parts: Any, tokens: int = 0) -> ModelResponse:
    """A ``ModelResponse`` carrying the given parts (and optional token usage)."""
    usage = RequestUsage(input_tokens=tokens, output_tokens=tokens) if tokens else RequestUsage()
    return ModelResponse(parts=list(parts), usage=usage)


def which_agent(info: AgentInfo) -> str:
    """``main`` / ``manual`` / ``obd`` from the tool set the model was given."""
    names = {t.name for t in info.function_tools}
    if "delegate_to_manual_agent" in names or "delegate_to_obd_agent" in names:
        return "main"
    if names and names <= {"list_manuals", "get_manual_toc", "read_manual_section", "search_manual_text"}:
        return "manual"
    if names and "list_signals" in names:
        return "obd"
    instructions = (info.instructions or "").lower()
    if "service-manual search specialist" in instructions:
        return "manual"
    if "obd-ii data investigation specialist" in instructions:
        return "obd"
    return "main"


def tool_returns(messages: List[ModelMessage]) -> List[ToolReturnPart]:
    out: List[ToolReturnPart] = []
    for m in messages:
        for p in getattr(m, "parts", []):
            if isinstance(p, ToolReturnPart):
                out.append(p)
    return out


def n_responses(messages: List[ModelMessage]) -> int:
    return sum(1 for m in messages if isinstance(m, ModelResponse))


class Script:
    """A scripted ``FunctionModel``: per agent, a list of responses in order.

    Once a list is exhausted the last entry is repeated (so a run that
    keeps asking gets the same answer again — useful for budget tests).
    """

    def __init__(self, main: List[ModelResponse], manual: Optional[List[ModelResponse]] = None,
                 obd: Optional[List[ModelResponse]] = None) -> None:
        self.scripts = {"main": main, "manual": manual or [], "obd": obd or []}
        self.calls: Dict[str, int] = {"main": 0, "manual": 0, "obd": 0}
        self.seen_tools: Dict[str, List[List[str]]] = {"main": [], "manual": [], "obd": []}

    def __call__(self, messages: List[ModelMessage], info: AgentInfo) -> ModelResponse:
        agent = which_agent(info)
        self.seen_tools[agent].append(sorted(t.name for t in info.function_tools))
        idx = self.calls[agent]
        self.calls[agent] += 1
        script = self.scripts[agent]
        if not script:
            return response(TextPart(content=f"(no script for {agent})"))
        return script[min(idx, len(script) - 1)]

    def model(self) -> FunctionModel:
        return FunctionModel(self, model_name=f"script-{id(self) % 1000}")


TEXT = TextPart
THINK = ThinkingPart
