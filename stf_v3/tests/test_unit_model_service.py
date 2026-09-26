"""PROD-11 model controller: T-14 (start once, failed start, fixed
commands, model name), T-15 (idle stop), T-16 (who holds the GPUs), plus
``reconcile`` against the throwaway database with a fake host.

Author: Xiangzhu Yan
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import httpx2 as httpx
import pytest

from stf_v3.diagnosis import model_service as ms
from tests.conftest import requires_db

NOW = dt.datetime(2026, 9, 25, 12, 0, tzinfo=dt.timezone.utc)
S = SimpleNamespace(llm_idle_stop_s=1800, llm_start_timeout_s=1500, llm_start_cooldown_s=900)
FREE = ms.GpuVerdict(free=True)


def obs(**kw: Any) -> ms.Observation:
    base = dict(container_running=False, ready=False, running_requests=0, gpu=FREE,
                eval_locked=False, demand=0, last_finished_at=None)
    base.update(kw)
    return ms.Observation(**base)


def ago(seconds: float) -> dt.datetime:
    return NOW - dt.timedelta(seconds=seconds)


# ── T-14 start / fail / cooldown ───────────────────────────────────────


def test_demand_and_free_gpus_start_once() -> None:
    """T-14 / FM-20: demand + free GPUs → start (state starting, ours); the
    next pass sees the container loading and does NOT start again."""
    d = ms.decide(obs(demand=1), ms.StateView(), S, NOW)
    assert d.action == "start" and d.fields["state"] == "starting" and d.fields["started_by_us"] is True
    st = ms.StateView(state="starting", started_by_us=True, requested_at=NOW)
    for _ in range(5):
        d2 = ms.decide(obs(demand=2, container_running=True), st, S, NOW + dt.timedelta(seconds=60))
        assert d2.action is None and d2.fields["state"] == "starting"


def test_no_demand_means_no_start_and_busy_gpus_mean_blocked() -> None:
    """D1: never start without a waiting diagnosis; never grab busy cards."""
    assert ms.decide(obs(), ms.StateView(), S, NOW).action is None
    busy = ms.GpuVerdict(free=False, reason="other_tenant")
    d = ms.decide(obs(demand=1, gpu=busy), ms.StateView(), S, NOW)
    assert d.action is None and d.fields["state"] == "blocked" and d.fields["blocked_reason"] == "other_tenant"


def test_failed_or_timed_out_start_sets_a_cooldown() -> None:
    """T-14 / FM-24: our start exited, or is still loading past the timeout →
    failed + cooldown (and the loading one is stopped); during the cooldown
    demand does not restart it; after it, it does."""
    st = ms.StateView(state="starting", started_by_us=True, requested_at=ago(60))
    d = ms.decide(obs(demand=1), st, S, NOW)                               # exited
    assert d.fields["state"] == "failed" and d.fields["cooldown_until"] == NOW + dt.timedelta(seconds=900)
    st2 = ms.StateView(state="starting", started_by_us=True, requested_at=ago(1600))
    d2 = ms.decide(obs(demand=1, container_running=True), st2, S, NOW)     # timed out
    assert d2.action == "stop" and d2.fields["state"] == "failed"
    cooling = ms.StateView(state="failed", cooldown_until=NOW + dt.timedelta(seconds=300))
    assert ms.decide(obs(demand=1), cooling, S, NOW).action is None
    cooled = ms.StateView(state="failed", cooldown_until=ago(1))
    assert ms.decide(obs(demand=1), cooled, S, NOW).action == "start"


async def test_readiness_checks_the_model_name() -> None:
    """T-14 / FM-26: a server on the port that serves another model is not
    'ready'; errors count as not ready."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "some/other-model"}]})

    settings = SimpleNamespace(llm_base_url="http://127.0.0.1:8010/v1", llm_api_key="none",
                               llm_model="Qwen/Qwen3.6-27B-FP8")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        assert await ms.model_ready(settings, client=c) is False
    ok = httpx.MockTransport(lambda r: httpx.Response(200, json={"data": [{"id": "Qwen/Qwen3.6-27B-FP8"}]}))
    async with httpx.AsyncClient(transport=ok) as c:
        assert await ms.model_ready(settings, client=c) is True
    bad = httpx.MockTransport(lambda r: httpx.Response(503))
    async with httpx.AsyncClient(transport=bad) as c:
        assert await ms.model_ready(settings, client=c) is False


async def test_only_the_two_fixed_commands_can_run(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """T-14 / FM-40 / FM-45: the controller runs exactly
    ``bash <repo>/infra/vllm_ctl.sh start|stop`` — nothing else, no arguments
    from the queue."""
    seen: List[List[str]] = []
    io = ms.HostIO(SimpleNamespace(vllm_ctl_path="", repo_dir="/home/x/repo"))

    async def fake_run(argv: List[str], timeout: float) -> Tuple[int, str, str]:
        seen.append(argv)
        return 0, "", ""

    monkeypatch.setattr(io, "_run", fake_run)
    await io.run_ctl("start")
    await io.run_ctl("stop")
    import pathlib

    ctl = str(pathlib.Path("/home/x/repo") / "infra" / "vllm_ctl.sh")
    assert seen == [["bash", ctl, "start"], ["bash", ctl, "stop"]]
    with pytest.raises(ValueError):
        await io.run_ctl("rm -rf /")
    from stf_v3.diagnosis.tasks import llm_reconcile

    import inspect
    params = list(inspect.signature(llm_reconcile.func).parameters)
    assert params == ["timestamp"]                     # periodic stamp only, no payload


def test_vllm_ctl_only_touches_its_own_compose_project() -> None:
    """FM-45: the control script pins the ``stf_llm`` project (never V1/V2's pod)."""
    import pathlib

    path = pathlib.Path(__file__).resolve().parents[2] / "infra" / "vllm_ctl.sh"
    if not path.is_file():
        pytest.skip("infra/ not present (portable copy)")
    script = path.read_text(encoding="utf-8")
    assert 'COMPOSE=("$PC" -p stf_llm -f "$DIR/docker-compose.vllm.yml")' in script


# ── T-15 idle stop ─────────────────────────────────────────────────────


@pytest.mark.parametrize("state, o, stops", [
    (ms.StateView(state="ready", started_by_us=True, ready_at=ago(4000), last_used_at=ago(1900)),
     obs(container_running=True, ready=True), True),
    (ms.StateView(state="ready", started_by_us=True, ready_at=ago(4000), last_used_at=ago(600)),
     obs(container_running=True, ready=True), False),                                  # not idle long enough
    (ms.StateView(state="ready", started_by_us=True, ready_at=ago(4000), last_used_at=ago(1900)),
     obs(container_running=True, ready=True, demand=1), False),                        # a diagnosis waits
    (ms.StateView(state="ready", started_by_us=False, ready_at=ago(4000), last_used_at=ago(9000)),
     obs(container_running=True, ready=True), False),                                  # started by hand
    (ms.StateView(state="ready", started_by_us=True, ready_at=ago(4000), last_used_at=ago(1900)),
     obs(container_running=True, ready=True, eval_locked=True), False),                # eval running
    (ms.StateView(state="ready", started_by_us=True, ready_at=ago(4000), last_used_at=ago(1900)),
     obs(container_running=True, ready=True, running_requests=2), False),              # request in flight
    (ms.StateView(state="ready", started_by_us=True, ready_at=ago(4000), last_used_at=ago(1900)),
     obs(container_running=True, ready=True, last_finished_at=ago(60)), False),        # just finished
])
def test_idle_stop_only_when_everything_agrees(state: ms.StateView, o: ms.Observation, stops: bool) -> None:
    """T-15 / FM-20 / FM-26: stop only a vLLM WE started, idle ≥ the limit,
    no unfinished diagnosis, no eval lock, no request in flight."""
    d = ms.decide(o, state, S, NOW)
    assert (d.action == "stop") is stops
    if stops:
        assert d.fields["state"] == "stopped" and d.fields["started_by_us"] is False


# ── T-16 who holds the GPUs ────────────────────────────────────────────

GPUS = [(0, "GPU-a", 0), (1, "GPU-b", 0)]


def _gpus(used0: int, used1: int) -> List[Tuple[int, str, int]]:
    return [(0, "GPU-a", used0), (1, "GPU-b", used1)]


@pytest.mark.parametrize("apps, procs, converting, free, reason", [
    ([], {}, False, True, None),
    ([("GPU-a", 11, 21000)], {11: ("max", "python train.py")}, False, False, "other_tenant"),
    ([("GPU-b", 12, 30000)], {12: ("talon", "mineru -p x.pdf")}, True, False, "manual_converting"),
    ([("GPU-a", 13, 36000), ("GPU-b", 14, 36000)],
     {13: ("talon", "python -m vllm.entrypoints.openai.api_server"),
      14: ("talon", "python -m vllm.entrypoints.openai.api_server")}, False, True, None),
    ([("GPU-a", 15, 57000)], {15: ("talon", "/bin/ollama runner")}, False, False, "ollama_loaded"),
    ([("GPU-a", 16, 21000)], {16: None}, False, False, "other_tenant"),              # owner unknown
    ([("GPU-a", 17, 500)], {17: ("max", "python small.py")}, False, True, None),      # below the line
])
def test_gpu_verdict_tells_ours_from_others(apps: Any, procs: Any, converting: bool, free: bool,
                                            reason: Optional[str]) -> None:
    """T-16 / FM-50 / FM-23: other teams, our MinerU, our Ollama and our own
    vLLM are told apart; an unknown owner is never grabbed."""
    used = {"GPU-a": sum(u for g, _, u in apps if g == "GPU-a"), "GPU-b": sum(u for g, _, u in apps if g == "GPU-b")}
    v = ms.classify_gpus(_gpus(used["GPU-a"], used["GPU-b"]), apps, procs, our_user="talon",
                         free_mib=2000, manual_converting=converting)
    assert v.free is free and v.reason == reason


def test_memory_no_process_accounts_for_blocks_as_another_tenant() -> None:
    """T-16: used memory on a card with no visible process → not free."""
    v = ms.classify_gpus(_gpus(21000, 0), [], {}, our_user="talon", free_mib=2000, manual_converting=False)
    assert not v.free and v.reason == "other_tenant" and v.snapshot[0]["kind"] == "unaccounted"


def test_wait_reason_mapping() -> None:
    """D2 / FM-31: controller silent → unresponsive; blocked → busy / manual;
    cooling down; starting with an ETA."""
    row = lambda **k: SimpleNamespace(**{**dict(state="stopped", blocked_reason=None, controller_seen_at=NOW,  # noqa: E731
                                                  cooldown_until=None, requested_at=None), **k})
    assert ms.wait_reason(None, NOW)[0] == "controller_unresponsive"
    assert ms.wait_reason(row(controller_seen_at=ago(600)), NOW)[0] == "controller_unresponsive"
    assert ms.wait_reason(row(state="blocked", blocked_reason="other_tenant"), NOW)[0] == "gpu_busy"
    assert ms.wait_reason(row(state="blocked", blocked_reason="manual_converting"), NOW)[0] == "manual_converting"
    assert ms.wait_reason(row(state="failed", cooldown_until=NOW + dt.timedelta(minutes=5)), NOW)[0] == "model_cooldown"
    reason, fmt = ms.wait_reason(row(state="starting", requested_at=ago(120)), NOW)
    assert reason == "model_starting" and fmt["eta_min"] == 10


# ── reconcile against the database (fake host) ─────────────────────────


class FakeIO(ms.HostIO):
    """Host double: scripted observations, records the commands."""

    def __init__(self, settings: Any, *, running: bool = False, ready: bool = False,
                 apps: Optional[List[Tuple[str, int, int]]] = None,
                 procs: Optional[Dict[int, Any]] = None, rc: int = 0) -> None:
        super().__init__(settings)
        self.running, self.is_ready, self.apps, self.procs, self.rc = running, ready, apps or [], procs or {}, rc
        self.commands: List[str] = []
        self.state_at_command: Optional[str] = None

    async def container_running(self) -> bool:
        return self.running

    async def ready(self) -> bool:
        return self.is_ready

    async def running_requests(self) -> int:
        return 0

    async def gpu_rows(self) -> List[Tuple[int, str, int]]:
        used = {g: 0 for _, g, _ in GPUS}
        for g, _, u in self.apps:
            used[g] += u
        return [(i, g, used[g]) for i, g, _ in GPUS]

    async def app_rows(self) -> List[Tuple[str, int, int]]:
        return self.apps

    async def proc_info(self, pid: int) -> Any:
        return self.procs.get(pid)

    def our_user(self) -> str:
        return "talon"

    def eval_locked(self) -> bool:
        return False

    async def run_ctl(self, action: str) -> Tuple[int, str]:
        from stf_v3.db import SessionLocal

        async with SessionLocal() as session:
            self.state_at_command = (await ms.get_state(session)).state
        self.commands.append(action)
        return self.rc, "boom" if self.rc else ""


@requires_db
async def test_reconcile_writes_state_before_the_command_and_records_failures(client, workshop_with_codes, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """T-14: with a waiting diagnosis and free GPUs, ``starting`` is committed
    BEFORE the start command runs (readers never see a stale 'stopped'); a
    failing command leaves ``failed`` + cooldown; a busy card → blocked."""
    from stf_v3.db import SessionLocal
    from stf_v3.settings import settings
    from tests.diagnosis_helpers import install_fake_queue, seed

    install_fake_queue(monkeypatch)
    wid, codes = workshop_with_codes
    s = await seed(client, wid, codes)
    r = await client.post(f"/v3/vehicles/{s.vehicle_id}/diagnose", headers=s.headers,
                          json={"obd_log_id": str(s.log_id)})
    assert r.status_code == 202
    io = FakeIO(settings)
    d = await ms.reconcile(SessionLocal, settings, io)
    assert d.action == "start" and io.commands == ["start"] and io.state_at_command == "starting"
    async with SessionLocal() as session:
        row = await ms.get_state(session)
        assert row.started_by_us and row.controller_seen_at is not None and row.gpu_snapshot == []
    # a start that fails
    async with SessionLocal() as session:
        await session.execute(ms.text("UPDATE model_service_state SET state = 'stopped', started_by_us = false"))
        await session.commit()
    io2 = FakeIO(settings, rc=1)
    await ms.reconcile(SessionLocal, settings, io2)
    async with SessionLocal() as session:
        row = await ms.get_state(session)
        assert row.state == "failed" and row.cooldown_until is not None and "rc=1" in row.failure_reason
    # busy card
    async with SessionLocal() as session:
        await session.execute(ms.text("UPDATE model_service_state SET state = 'stopped', cooldown_until = NULL"))
        await session.commit()
    io3 = FakeIO(settings, apps=[("GPU-a", 9, 21000)], procs={9: ("max", "python train.py")})
    d3 = await ms.reconcile(SessionLocal, settings, io3)
    assert d3.action is None and io3.commands == []
    async with SessionLocal() as session:
        row = await ms.get_state(session)
        assert row.state == "blocked" and row.blocked_reason == "other_tenant"
