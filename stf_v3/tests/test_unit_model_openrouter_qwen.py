"""PROD-10 D5 follow-up: the local Qwen model, reached through OpenRouter.

While the shared GPUs are busy, the OBD prompt fix can be pre-checked on
the SAME model (``qwen/qwen3.6-27b``, FP8 hosts) through the cloud
comparison path.  A probe on 2026-09-24 showed OpenRouter thinks by
default, honours ``reasoning: {enabled: false}`` and ignores vLLM's
``chat_template_kwargs``; it also routes each request to whichever host it
likes.  So the ``qwen-openrouter`` profile sends the reasoning switch, can
pin one host, and uses the qwen-vllm budgets (comparable runs).  The run
still counts as ``cloud`` and never for the merge gate.

Author: Xiangzhu Yan
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import httpx2 as httpx
import pytest
from pydantic_ai import Agent

from stf_v3.diagnosis.agent import model as m
from stf_v3.diagnosis.agent.deps import Budgets
from stf_v3.evals import cli as evcli
from stf_v3.evals import gate
from stf_v3.settings import Settings

QWEN_API = dict(cloud_llm_enabled=True, cloud_llm_api_key="k", cloud_llm_model="qwen/qwen3.6-27b")


def _capture(store: List[Dict[str, Any]]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        store.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "x", "object": "chat.completion", "created": 0, "model": "m",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _cloud_body(settings: Settings, *, subagent: bool = False) -> Dict[str, Any]:
    bodies: List[Dict[str, Any]] = []
    model = m.build_model(settings, cloud=True, http_client=_capture(bodies))
    agent = Agent(model, instructions="test")
    await agent.run("hi", model_settings=m.model_settings(settings, model=model, subagent=subagent))
    assert len(bodies) == 1
    return bodies[0]


async def test_qwen_via_openrouter_switches_thinking_off_the_openrouter_way() -> None:
    """``reasoning.enabled=false`` is sent; vLLM's template switch and
    OpenAI's ``reasoning_effort`` are not; no host pin by default."""
    body = await _cloud_body(Settings(**QWEN_API))
    assert body["model"] == "qwen/qwen3.6-27b"
    assert body["reasoning"] == {"enabled": False}
    assert "chat_template_kwargs" not in body and "reasoning_effort" not in body and "provider" not in body


async def test_a_pinned_host_is_sent_without_fallbacks() -> None:
    """``cloud_llm_provider`` pins one OpenRouter host (reproducible runs);
    sub-agent requests carry the same body."""
    body = await _cloud_body(Settings(**QWEN_API, cloud_llm_provider="DeepInfra"), subagent=True)
    assert body["provider"] == {"order": ["DeepInfra"], "allow_fallbacks": False}
    assert body["reasoning"] == {"enabled": False}
    assert body["temperature"] == 0.2


async def test_other_cloud_models_still_get_a_plain_request() -> None:
    """The generic cloud path is unchanged (deepseek comparison, FM-29)."""
    body = await _cloud_body(Settings(cloud_llm_enabled=True, cloud_llm_api_key="k", cloud_llm_provider="DeepInfra"))
    assert body["model"] == "deepseek/deepseek-v3.2"
    assert "reasoning" not in body and "provider" not in body and "chat_template_kwargs" not in body


def test_qwen_via_openrouter_takes_the_local_budgets() -> None:
    """Same model, same gates: the profile row equals qwen-vllm's (300 s / 20
    requests for a sub-agent), not the generic cloud row (240 s / 12)."""
    assert m.profile_defaults(m.PROFILE_QWEN_OPENROUTER) == m.profile_defaults(m.PROFILE_QWEN_VLLM)
    b = Budgets.from_settings(Settings(**QWEN_API), m.PROFILE_QWEN_OPENROUTER)
    assert (b.subagent_wall_clock_s, b.subagent_request_limit) == (300.0, 20)
    assert m.select_profile(Settings(**QWEN_API), cloud=True) == m.PROFILE_QWEN_OPENROUTER
    assert m.select_profile(Settings(**QWEN_API)) == m.PROFILE_QWEN_VLLM   # the local model is untouched


def test_the_product_path_never_resolves_to_the_openrouter_profile_by_accident() -> None:
    """The local settings keep the vLLM profile; the product path to a cloud
    endpoint still needs the explicit allow-cloud switch."""
    with pytest.raises(m.ModelConfigError):
        m.build_model(Settings(llm_base_url="https://openrouter.ai/api/v1", llm_model="qwen/qwen3.6-27b"))


def test_cloud_model_options_need_cloud_and_a_cloud_run_never_counts() -> None:
    """``--cloud-model`` / ``--cloud-provider`` are refused without
    ``--cloud``; a scorecard from such a run is ineligible for the gate."""
    assert evcli.main(["run", "--purpose", "comparison", "--cloud-model", "qwen/qwen3.6-27b"]) == 4
    assert evcli.main(["run", "--purpose", "comparison", "--cloud-provider", "DeepInfra"]) == 4
    card = {"meta": {"mode": {"backend": "cloud", "thinking": "off", "purpose": "gate", "budget_scale": 1.0},
                     "complete": True, "valid": True, "config": {"judge_model": "z-ai/glm-5.1"}}}
    ok, why = gate.eligibility(card, "z-ai/glm-5.1")
    assert not ok and any("only local counts" in w for w in why)
