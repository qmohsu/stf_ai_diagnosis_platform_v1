"""PROD-09 T-7: the server run script's cloud switch, endpoint wait and file names.

Drives ``_preflight_model`` against a mock HTTP transport with a fake clock
(no network, no sleeping).

Author: Xiangzhu Yan
"""

from __future__ import annotations

import importlib.util
import pathlib
import uuid
from typing import Any, Dict, List

import httpx
import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "diagnose_once.py"
_spec = importlib.util.spec_from_file_location("diagnose_once", _SCRIPT)
assert _spec and _spec.loader
diagnose_once: Any = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(diagnose_once)

MODEL = "Qwen/Qwen3.6-27B-FP8"


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: List[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s


def _endpoint(plan: List[Any], posts: List[Dict[str, Any]]) -> httpx.MockTransport:
    """``plan`` = per-GET outcome: a list of served ids, or an int HTTP status."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts.append({"url": str(request.url)})
            return httpx.Response(200, json={"choices": [{"message": {"content": "ready"}}]})
        step = plan[min(calls["n"], len(plan) - 1)]
        calls["n"] += 1
        if isinstance(step, int):
            return httpx.Response(step, json={"error": "not ready"})
        return httpx.Response(200, json={"data": [{"id": i} for i in step]})
    return httpx.MockTransport(handler)


def test_cloud_flag_and_wait_default() -> None:
    """``--cloud`` selects the comparison model; ``--model-wait-s`` is optional."""
    a = diagnose_once._parse_args(["--vehicle-id", str(uuid.uuid4()), "--log-id", str(uuid.uuid4()), "--cloud"])
    assert a.cloud is True and a.model_wait_s is None and a.no_warmup is False


def test_model_missing_without_wait_fails_fast_and_names_it() -> None:
    """FM-35: served list lacks the configured name → RuntimeError naming the
    model and what IS served; no warm-up request is made."""
    posts: List[Dict[str, Any]] = []
    clock = _Clock()
    with pytest.raises(RuntimeError) as exc:
        diagnose_once._preflight_model("http://127.0.0.1:8010/v1", "none", MODEL, warmup=True, wait_s=0,
                                       transport=_endpoint([["other-model"]], posts), sleep=clock.sleep, now=clock.now)
    assert MODEL in str(exc.value) and "other-model" in str(exc.value)
    assert posts == [] and clock.sleeps == []


def test_waits_through_a_cold_start_then_warms_up() -> None:
    """FM-40: 503s for a while, then the model appears → the preflight waits
    (bounded, polling) and returns the seconds waited; warm-up runs once."""
    posts: List[Dict[str, Any]] = []
    clock = _Clock()
    waited = diagnose_once._preflight_model(
        "http://127.0.0.1:8010/v1", "none", MODEL, warmup=True, wait_s=60, poll_s=5,
        transport=_endpoint([503, 503, 503, [MODEL]], posts), sleep=clock.sleep, now=clock.now)
    assert waited == 15 and clock.sleeps == [5, 5, 5]
    assert len(posts) == 1 and posts[0]["url"].endswith("/chat/completions")


def test_never_ready_gives_up_after_the_wait_limit() -> None:
    """FM-40: an endpoint that never lists the model fails after ``wait_s``
    with the wait in the message — no hang, no warm-up."""
    posts: List[Dict[str, Any]] = []
    clock = _Clock()
    with pytest.raises(RuntimeError) as exc:
        diagnose_once._preflight_model("http://127.0.0.1:8010/v1", "none", MODEL, warmup=True, wait_s=20, poll_s=5,
                                       transport=_endpoint([503], posts), sleep=clock.sleep, now=clock.now)
    assert "waited 20s" in str(exc.value) and clock.sleeps == [5, 5, 5, 5] and posts == []


def test_cloud_preflight_checks_once_without_warmup() -> None:
    """Cloud: the model list is checked once (wait 0) and no completion is
    bought just to warm up."""
    posts: List[Dict[str, Any]] = []
    clock = _Clock()
    waited = diagnose_once._preflight_model("https://openrouter.ai/api/v1", "k", "deepseek/deepseek-v3.2",
                                            warmup=False, wait_s=0, transport=_endpoint([["deepseek/deepseek-v3.2"]], posts),
                                            sleep=clock.sleep, now=clock.now)
    assert waited == 0 and posts == []


def test_output_prefix_carries_local_or_cloud_and_never_the_vin(tmp_path: pathlib.Path) -> None:
    """FM-2 / FM-25: names end in ``_local`` / ``_cloud`` and contain only the
    stamp and the log id prefix."""
    log_id = uuid.UUID("12345678-aaaa-bbbb-cccc-1234567890ab")
    local = diagnose_once._run_prefix(tmp_path, log_id, False, stamp="20260918T000000Z")
    cloud = diagnose_once._run_prefix(tmp_path, log_id, True, stamp="20260918T000000Z")
    assert local.name == "run_20260918T000000Z_12345678_local"
    assert cloud.name == "run_20260918T000000Z_12345678_cloud" and local != cloud
