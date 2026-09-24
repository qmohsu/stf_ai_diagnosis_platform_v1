"""PROD-09 T-1 … T-6: adapter profiles, request contract, residue filters,
configuration / keys and per-profile budgets (all offline).

Author: Xiangzhu Yan
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import httpx2 as httpx
import pytest
import structlog
from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ThinkingPart, UserPromptPart

from stf_v3.diagnosis.agent import events as ev
from stf_v3.diagnosis.agent import model as m
from stf_v3.diagnosis.agent.deps import Budgets
from stf_v3.diagnosis.agent.main_agent import run_diagnosis
from stf_v3.diagnosis.agent.report import (
    TOOL_CALL_RESIDUE_LIMITATION,
    has_tool_call_residue,
    strip_thinking_residue,
)
from stf_v3.settings import Settings
from tests.agent_helpers import FAKE_VIN, TEXT, THINK, Script, make_deps, response

VLLM = "http://127.0.0.1:8010/v1"
OLLAMA = "http://127.0.0.1:11434/v1"
CLOUD = "https://openrouter.ai/api/v1"


# ───────────────────────── T-1 profile selection ─────────────────────────

@pytest.mark.parametrize(
    "base_url, model_name, explicit, expected",
    [
        (VLLM, "Qwen/Qwen3.6-27B-FP8", "auto", m.PROFILE_QWEN_VLLM),
        (VLLM, "QWEN/QWEN3.6-27B-FP8", "auto", m.PROFILE_QWEN_VLLM),          # case
        ("http://localhost:8010/v1", "qwen36", "auto", m.PROFILE_QWEN_VLLM),   # alias, no tag
        (OLLAMA, "qwen3.5:27b-q8_0", "auto", m.PROFILE_QWEN_OLLAMA),           # Ollama port
        (VLLM, "qwen3.5:27b-q8_0", "auto", m.PROFILE_QWEN_OLLAMA),             # Ollama tag on another port
        (VLLM, "llama-3-70b", "auto", m.PROFILE_GENERIC),                      # unknown local model
        (CLOUD, "qwen/qwen3.6-27b", "auto", m.PROFILE_QWEN_OPENROUTER),        # same model via API (D5)
        (CLOUD, "qwen/qwen3.5-plus", "auto", m.PROFILE_QWEN_OPENROUTER),       # any Qwen on OpenRouter
        ("https://dashscope.example.com/v1", "qwen-plus", "auto", m.PROFILE_GENERIC),  # other cloud: generic
        (CLOUD, "deepseek/deepseek-v3.2", "auto", m.PROFILE_GENERIC),
        (VLLM, "Qwen/Qwen3.6-27B-FP8", "qwen-ollama", m.PROFILE_QWEN_OLLAMA),  # explicit wins
    ],
)
def test_resolve_profile_table(base_url: str, model_name: str, explicit: str, expected: str) -> None:
    """FM-7 / FM-30: URL + name pick the profile; an explicit profile wins."""
    assert m.resolve_profile(base_url, model_name, explicit) == expected


def test_unknown_explicit_profile_is_refused_and_local_generic_warns() -> None:
    """A typo in STF_V3_LLM_PROFILE fails loudly; a local endpoint that lands
    on ``generic`` logs a warning (no thinking control)."""
    with pytest.raises(m.ModelConfigError):
        m.resolve_profile(VLLM, "Qwen/Qwen3.6-27B-FP8", "qwen-vlm")
    with structlog.testing.capture_logs() as logs:
        assert m.resolve_profile(VLLM, "mystery-model", "auto") == m.PROFILE_GENERIC
        assert m.resolve_profile(CLOUD, "mystery-model", "auto") == m.PROFILE_GENERIC
    warnings = [l for l in logs if l.get("event") == "model.profile_generic"]
    assert len(warnings) == 1 and warnings[0]["model"] == "mystery-model"


def test_model_source_is_recorded_on_the_model_object() -> None:
    """FM-31: the built model carries kind / host / profile; the label never
    carries the key; test models have no source (label ``test``)."""
    local = m.build_model(Settings())
    src = m.source_of(local)
    assert src is not None and src.kind == "local" and src.is_local
    assert src.profile == m.PROFILE_QWEN_VLLM and src.host == "127.0.0.1:8010"
    assert m.source_label(local) == "local qwen-vllm @127.0.0.1:8010"
    cloud = m.build_model(Settings(cloud_llm_enabled=True, openrouter_api_key="sk-or-secret-123"), cloud=True)
    csrc = m.source_of(cloud)
    assert csrc is not None and csrc.kind == "cloud" and not csrc.is_local and csrc.profile == m.PROFILE_GENERIC
    assert csrc.host == "openrouter.ai" and "secret" not in m.source_label(cloud)
    assert "secret" not in json.dumps(csrc.to_dict())
    assert m.source_of(Script(main=[]).model()) is None and m.source_label(None) == "test"


# ───────────────────────── T-2 request contract ─────────────────────────

def _capture(store: List[Dict[str, Any]]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        store.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "x", "object": "chat.completion", "created": 0, "model": "m",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _lookup(code: str) -> str:
    """Explain a code.

    Args:
        code: The code.
    """
    return code


async def _one_request(settings: Settings, *, cloud: bool = False, history: Any = None) -> Dict[str, Any]:
    bodies: List[Dict[str, Any]] = []
    model = m.build_model(settings, cloud=cloud, http_client=_capture(bodies))
    agent = Agent(model, instructions="test", tools=[_lookup])
    await agent.run("hi", message_history=history, model_settings=m.model_settings(settings, model=model))
    assert len(bodies) == 1
    return bodies[0]


async def test_vllm_profile_sends_only_the_chat_template_switch() -> None:
    """FM-29 / FM-11: qwen-vllm adds ``chat_template_kwargs.enable_thinking=false``
    and nothing OpenAI-specific (no reasoning_effort); tools are not strict."""
    body = await _one_request(Settings())
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert "reasoning_effort" not in body and "thinking" not in body
    assert body.get("max_completion_tokens", body.get("max_tokens")) == 8192 and body["temperature"] == 0.3
    assert all(not t["function"].get("strict") for t in body["tools"])


async def test_ollama_and_cloud_profiles_send_no_vendor_fields() -> None:
    """The Ollama fallback and every cloud model get a plain OpenAI request."""
    ollama = await _one_request(Settings(llm_base_url=OLLAMA, llm_model="qwen3.5:27b-q8_0"))
    cloud = await _one_request(Settings(cloud_llm_enabled=True, cloud_llm_api_key="k"), cloud=True)
    for body in (ollama, cloud):
        assert "chat_template_kwargs" not in body and "reasoning_effort" not in body
        assert all(not t["function"].get("strict") for t in body["tools"])
    assert ollama["model"] == "qwen3.5:27b-q8_0" and cloud["model"] == "deepseek/deepseek-v3.2"


async def test_thinking_parts_are_never_sent_back() -> None:
    """FM-43 (PROD-08) still holds under every profile: earlier thinking is
    not part of the next request."""
    history = [
        ModelRequest(parts=[UserPromptPart(content="earlier")]),
        ModelResponse(parts=[ThinkingPart(content="SECRET-REASONING"), TextPart(content="earlier answer")]),
    ]
    for settings, cloud in ((Settings(), False),
                            (Settings(llm_base_url=OLLAMA, llm_model="qwen3.5:27b-q8_0"), False),
                            (Settings(cloud_llm_enabled=True, cloud_llm_api_key="k"), True)):
        body = await _one_request(settings, cloud=cloud, history=history)
        assert "SECRET-REASONING" not in json.dumps(body)
        assert any(msg.get("content") == "earlier answer" for msg in body["messages"])


# ───────────────────────── T-3 / T-4 residue filters ─────────────────────────

def test_strip_thinking_residue_only_removes_complete_tagged_blocks() -> None:
    """FM-1: tagged blocks go (any case, any of three tag names); prose that
    mentions thinking, an unterminated tag, or angle brackets in code stay."""
    clean, hits, removed = strip_thinking_residue("<think>\nplan\n</think>\n# Report\nbody")
    assert clean == "# Report\nbody" and hits == 1 and removed > 0
    untouched = "I was thinking about <think and `<tag>` in code; see <b>bold</b>."
    assert strip_thinking_residue(untouched) == (untouched, 0, 0)
    clean, hits, _ = strip_thinking_residue("<THINKING>x</THINKING>rest <reasoning>y</reasoning> end")
    assert clean == "rest end" and hits == 2
    assert has_tool_call_residue('<tool_call>{"name": "x"}</tool_call>')
    assert has_tool_call_residue("<function=list_dtcs>") and not has_tool_call_residue("no markup here")


async def test_run_strips_residue_and_counts_thinking_in_done_event() -> None:
    """FM-1 / FM-18: a scripted model that thinks AND leaks a tagged block:
    the report is clean, the counters land in the report and the ``done``
    event, thinking is a reasoning event only."""
    script = Script(main=[response(THINK(content="hidden-plan"), TEXT(content="<think>leak</think>\n# 診斷\n正常"))])
    out = await run_diagnosis(make_deps(), script.model())
    assert out.report.content_md == "# 診斷\n正常" and not out.report.partial
    assert out.report.filter_hits == 1 and out.report.filter_removed_chars == len("<think>leak</think>\n")
    assert out.report.thinking_chars == len("hidden-plan") and out.report.model_source == "test"
    done = out.events[-1]
    assert done.event_type == ev.DONE and done.payload["thinking_chars"] == len("hidden-plan")
    assert done.payload["filter_hits"] == 1 and done.payload["partial"] is False
    start = out.events[0]
    assert start.payload["model_source"] == "test" and start.payload["profile"] == "test"
    assert not any(e.event_type == ev.TOKEN and "hidden-plan" in e.payload["text"] for e in out.events)


async def test_unparsed_tool_call_markup_makes_the_report_partial() -> None:
    """FM-41: tool-call XML as the final answer → partial report + limitation,
    no fabricated citations."""
    script = Script(main=[response(TEXT(content='<tool_call>\n{"name": "list_dtcs", "arguments": {}}\n</tool_call>'))])
    out = await run_diagnosis(make_deps(), script.model())
    assert out.stopped_reason == "complete" and out.report.partial
    assert TOOL_CALL_RESIDUE_LIMITATION in out.report.limitations
    assert out.report.content_md.startswith("> **Partial report**") and out.report.citations == []
    assert out.events[-1].payload["partial"] is True


# ───────────────────────── T-5 configuration / keys ─────────────────────────

def test_cloud_key_falls_back_to_the_openrouter_key() -> None:
    """FM-32: one key source — the comparison key, else the manual-summary key."""
    assert Settings(openrouter_api_key="sk-or").cloud_api_key == "sk-or"
    assert Settings(cloud_llm_api_key="own", openrouter_api_key="sk-or").cloud_api_key == "own"
    with pytest.raises(m.ModelConfigError):
        m.build_model(Settings(openrouter_api_key="sk-or"), cloud=True)          # not enabled
    with pytest.raises(m.ModelConfigError):
        m.build_model(Settings(cloud_llm_enabled=True), cloud=True)              # no key anywhere
    assert m.build_model(Settings(cloud_llm_enabled=True, openrouter_api_key="sk-or"), cloud=True)


def test_product_path_to_cloud_needs_the_explicit_switch_and_pseudonymises() -> None:
    """FM-12 / FM-14 / FM-33: the product path refuses a remote address; with
    the switch it is recorded as ``cloud`` and prompts carry the pseudonym."""
    with pytest.raises(m.ModelConfigError):
        m.build_model(Settings(llm_base_url=CLOUD, llm_api_key="k"))
    allowed = m.build_model(Settings(llm_base_url=CLOUD, llm_api_key="k", llm_allow_cloud=True))
    src = m.source_of(allowed)
    # The local model name on OpenRouter resolves to qwen-openrouter (thinking
    # off, local budgets) -- still recorded as cloud, still behind the switch.
    assert src is not None and src.kind == "cloud" and src.profile == m.PROFILE_QWEN_OPENROUTER
    other = m.source_of(m.build_model(Settings(llm_base_url=CLOUD, llm_model="deepseek/deepseek-v3.2",
                                               llm_api_key="k", llm_allow_cloud=True)))
    assert other is not None and other.profile == m.PROFILE_GENERIC
    assert not m.model_is_local(Settings(llm_base_url=CLOUD, llm_allow_cloud=True))
    assert not m.model_is_local(Settings(), cloud=True)
    deps = make_deps(model_is_local=False)
    assert FAKE_VIN not in deps.vehicle_label() and "V-" in deps.vehicle_label()
    assert FAKE_VIN in make_deps(model_is_local=True).vehicle_label()


# ───────────────────────── T-6 budgets per profile ─────────────────────────

def test_budgets_follow_the_profile_unless_set_explicitly() -> None:
    """FM-8: None settings take the profile default (vLLM ≠ Ollama);
    an explicit setting always wins; request timeout likewise."""
    vllm = Budgets.from_settings(Settings())
    ollama = Budgets.from_settings(Settings(llm_base_url=OLLAMA, llm_model="qwen3.5:27b-q8_0"))
    assert (vllm.wall_clock_s, vllm.request_limit) == (900.0, 60)
    assert (ollama.wall_clock_s, ollama.request_limit) == (1200.0, 80)
    explicit = Budgets.from_settings(Settings(agent_wall_clock_s=42, subagent_request_limit=3))
    assert explicit.wall_clock_s == 42 and explicit.subagent_request_limit == 3
    assert Budgets.from_settings(Settings(), m.PROFILE_QWEN_OLLAMA).wall_clock_s == 1200.0
    assert m.model_settings(Settings())["timeout"] == 180.0
    assert m.model_settings(Settings(llm_base_url=OLLAMA, llm_model="qwen3.5:27b-q8_0"))["timeout"] == 300.0
    assert m.model_settings(Settings(llm_request_timeout_s=7))["timeout"] == 7
    sub = m.model_settings(Settings(), subagent=True)
    assert sub["max_tokens"] == 12_288 and sub["temperature"] == 0.2
    with pytest.raises(m.ModelConfigError):
        m.profile_defaults("nope")
