"""PROD-08 T-5 / T-14: agent tool contracts and hygiene.

* every parameter of the 12 tools carries a description (FM-49);
* sub-agents carry only their own tools, never delegation (FM-36);
* tool and agent modules never touch the database (FM-50);
* importing the runtime needs no network (FM-15);
* the model source refuses a non-local endpoint without opt-in (FM-19).

Author: Xiangzhu Yan
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

from stf_v3.diagnosis.agent import main_agent, manual_agent, obd_agent
from stf_v3.diagnosis.agent.model import ModelConfigError, build_model, model_is_local
from stf_v3.settings import Settings, is_local_url

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "stf_v3" / "diagnosis"


def _tool_defs(agent):  # type: ignore[no-untyped-def]
    toolset = agent._function_toolset  # noqa: SLF001 — inspection only
    return {name: tool for name, tool in toolset.tools.items()}


def test_every_tool_parameter_has_a_description() -> None:
    """FM-49: the schema the model sees names every parameter with the V2
    description; 12 tools on the main agent."""
    tools = _tool_defs(main_agent.MAIN_AGENT)
    assert len(tools) == 12
    for name, tool in tools.items():
        schema = tool.function_schema.json_schema
        props = schema.get("properties", {})
        assert props, name
        for pname, pschema in props.items():
            assert pschema.get("description"), f"{name}.{pname} has no description"
        assert tool.description, name


def test_subagents_only_carry_their_own_tools() -> None:
    """FM-36: manual sub-agent = 4 manual tools, OBD sub-agent = 6 OBD tools,
    neither has a delegation tool (no recursion)."""
    manual = set(_tool_defs(manual_agent.MANUAL_AGENT))
    obd = set(_tool_defs(obd_agent.OBD_AGENT))
    # + final_answer, offered only on the force-final turn (PROD-10)
    assert manual == {"list_manuals", "get_manual_toc", "read_manual_section", "search_manual_text", "final_answer"}
    assert obd == {"list_signals", "read_window", "get_signal_stats", "find_events", "list_dtcs", "lookup_dtc"}
    assert not ({"delegate_to_manual_agent", "delegate_to_obd_agent"} & (manual | obd))
    assert set(main_agent.MAIN_TOOL_NAMES) == (manual - {"final_answer"}) | obd | {"delegate_to_manual_agent",
                                                                                "delegate_to_obd_agent"}


def test_tools_and_agent_modules_never_import_the_database() -> None:
    """FM-50: only ``bootstrap.py`` (row → deps) may import the DB layer."""
    offenders = []
    for path in SRC.rglob("*.py"):
        if path.name in ("bootstrap.py", "models.py"):   # row → deps, and the ORM tables
            continue
        text = path.read_text(encoding="utf-8")
        if "from stf_v3.db" in text or "import stf_v3.db" in text or "SessionLocal" in text:
            offenders.append(str(path.relative_to(SRC)))
    assert offenders == []


def test_runtime_imports_without_network() -> None:
    """FM-15: importing the whole runtime with sockets disabled succeeds (no
    tokenizer download, no endpoint probe at import time)."""
    code = (
        "import socket\n"
        "def _no(*a, **k): raise RuntimeError('network access during import')\n"
        "socket.socket.connect = _no\n"
        "socket.create_connection = _no\n"
        "socket.getaddrinfo = _no\n"
        "import stf_v3.diagnosis.agent.main_agent, stf_v3.diagnosis.agent.model\n"
        "print('ok')\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120,
                          env={**__import__('os').environ, "PYDANTIC_AI_NO_BANNER": "1"})
    assert proc.returncode == 0 and "ok" in proc.stdout, proc.stderr[-2000:]


def test_model_source_refuses_cloud_without_opt_in() -> None:
    """FM-19: a remote base URL is rejected unless ``llm_allow_cloud``; local
    addresses are recognised; the cloud model needs its own switch + key."""
    assert is_local_url("http://127.0.0.1:11434/v1") and is_local_url("http://localhost:8000/v1")
    assert is_local_url("http://10.0.0.5:8000/v1") and not is_local_url("https://openrouter.ai/api/v1")
    remote = Settings(llm_base_url="https://openrouter.ai/api/v1", llm_api_key="x")
    with pytest.raises(ModelConfigError):
        build_model(remote)
    assert not model_is_local(remote)
    allowed = Settings(llm_base_url="https://openrouter.ai/api/v1", llm_api_key="x", llm_allow_cloud=True)
    assert build_model(allowed).model_name == allowed.llm_model
    local = Settings()
    assert model_is_local(local) and build_model(local).model_name == "Qwen/Qwen3.6-27B-FP8"
    with pytest.raises(ModelConfigError):
        build_model(Settings(), cloud=True)
