"""PROD-09 T-8: the vLLM deployment file's key parameters (static check).

Skipped when the infra directory is absent (the portable check copies
only ``stf_v3/``).

Author: Xiangzhu Yan
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any, Dict, List

import pytest
import yaml

from stf_v3.settings import Settings

_INFRA = pathlib.Path(__file__).resolve().parents[2] / "infra"
_COMPOSE = _INFRA / "docker-compose.vllm.yml"
_CTL = _INFRA / "vllm_ctl.sh"
_UNIT = _INFRA / "stf-llm.service"

pytestmark = pytest.mark.skipif(not _COMPOSE.is_file(), reason="infra/ not present (portable copy)")


def _default(value: str) -> str:
    """``${VAR:-default}`` → ``default``; plain values unchanged."""
    m = re.fullmatch(r"\$\{[A-Z_]+:-(.*)\}", value)
    return m.group(1) if m else value


def _args(cmd: List[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    i = 0
    while i < len(cmd):
        key = cmd[i]
        if i + 1 < len(cmd) and not cmd[i + 1].startswith("--"):
            out[key] = _default(cmd[i + 1])
            i += 2
        else:
            out[key] = True
            i += 1
    return out


def test_vllm_service_parameters() -> None:
    """FM-34 / FM-26 / FM-10 / FM-35 / FM-6 / FM-37: pinned image, offline
    weights, D2 memory share, two cards, 98k context, thinking off, qwen
    tool parser, prefix caching, loopback port, model name == V3 default,
    long health start period, bounded restarts, external weight volume."""
    doc = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    svc = doc["services"]["stf-vllm"]
    assert svc["image"] == "docker.io/vllm/vllm-openai:v0.24.0-ubuntu2404"
    assert svc["container_name"] == "stf-vllm" and svc["network_mode"] == "host" and svc["runtime"] == "nvidia"
    assert str(svc["environment"]["HF_HUB_OFFLINE"]) == "1"
    assert "vllm_hf_cache:/root/.cache/huggingface" in svc["volumes"]
    assert doc["volumes"]["vllm_hf_cache"] == {"external": True}
    a = _args(list(svc["command"]))
    assert a["--gpu-memory-utilization"] == "0.80"                 # D2
    assert a["--tensor-parallel-size"] == "2" and a["--max-model-len"] == "98304"
    assert a["--host"] == "127.0.0.1" and a["--port"] == "8010"
    assert a["--served-model-name"] == a["--model"] == Settings().llm_model   # FM-35
    assert Settings().llm_base_url == "http://127.0.0.1:8010/v1"
    assert json.loads(a["--default-chat-template-kwargs"]) == {"enable_thinking": False}
    assert a["--reasoning-parser"] == "qwen3" and a["--tool-call-parser"] == "qwen3_xml"
    assert a["--enable-auto-tool-choice"] is True and a["--enable-prefix-caching"] is True
    assert json.loads(a["--speculative-config"])["method"] == "mtp"
    hc = svc["healthcheck"]
    assert int(hc["start_period"].rstrip("s")) >= 360 and hc["test"][0] == "CMD"
    assert str(svc["restart"]).startswith("on-failure:")


def test_control_script_pins_the_project_name_and_unit_calls_it() -> None:
    """FM-36 / FM-21: the only way to start vLLM uses the fixed project name;
    the systemd unit template starts/stops through the same script."""
    ctl = _CTL.read_text(encoding="utf-8")
    assert "-p stf_llm" in ctl and "docker-compose.vllm.yml" in ctl
    for verb in ("start)", "stop)", "wait)", "status)", "install-unit)"):
        assert verb in ctl
    unit = _UNIT.read_text(encoding="utf-8")
    assert "vllm_ctl.sh start" in unit and "vllm_ctl.sh stop" in unit and "WantedBy=default.target" in unit
