"""On-demand model service (PROD-11 D1 / D2).

vLLM is started on demand and never kept resident (user decision
2026-09-24).  Two halves share this module:

* **Diagnosis side** (container worker): ``model_ready`` asks the
  endpoint whether the configured model is served (the model NAME must
  match, FM-26); ``wait_reason`` turns the controller's state row into
  the ``waiting`` event reason the user sees (D2, FM-31).
* **Controller** (host GPU worker, the only process that may run host
  commands): ``reconcile`` observes the vLLM container, its readiness and
  in-flight requests, who holds GPU memory, whether an eval holds its
  lock, and how many diagnoses are unfinished; ``decide`` (pure, so it
  is tested exhaustively) chooses start / stop / nothing; the fixed
  commands ``vllm_ctl.sh start|stop`` are the only things it can run —
  the task takes no arguments (FM-40).

Start only when both GPUs are free of anyone else's memory (other
tenants, our own MinerU or Ollama, FM-50 / FM-45); never start twice
while loading (FM-20); a failed or timed-out start sets a cooldown
(FM-24); stop only a vLLM we started ourselves, idle for
``llm_idle_stop_s`` with no unfinished diagnosis, no eval lock and no
request in flight (FM-20 / FM-26).  Everything the decision needs across
restarts lives in ``model_service_state`` (FM-32).

Author: Xiangzhu Yan
"""

from __future__ import annotations

import asyncio
import datetime as dt
import getpass
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import httpx
import structlog
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stf_v3.diagnosis.models import ACTIVE_STATUSES, DiagnosisConversation, ModelServiceState

log = structlog.get_logger(__name__)

CONTROLLER_STALE_S = 180          # no controller heartbeat for 3 min → "unresponsive"
STOPPED_EXTERNALLY = "stopped externally (not by the controller)"
VLLM_CONTAINER = "stf-vllm"
COLD_START_S = 720                # typical cold start (10–12 min) for the ETA


# ── diagnosis side ────────────────────────────────────────────────────


async def model_ready(settings: Any, *, client: Optional[httpx.AsyncClient] = None) -> bool:
    """Whether the endpoint serves the configured model (name checked, FM-26)."""
    url = settings.llm_base_url.rstrip("/") + "/models"
    headers = {}
    if settings.llm_api_key and settings.llm_api_key != "none":
        headers["Authorization"] = f"Bearer {settings.llm_api_key}"
    try:
        if client is not None:
            resp = await client.get(url, headers=headers, timeout=5.0)
        else:
            async with httpx.AsyncClient(timeout=5.0) as c:
                resp = await c.get(url, headers=headers)
        if resp.status_code != 200:
            return False
        ids = {m.get("id") for m in resp.json().get("data", [])}
        return settings.llm_model in ids
    except (httpx.HTTPError, ValueError, AttributeError):
        return False


async def get_state(session: AsyncSession) -> Optional[ModelServiceState]:
    """The controller's state row (None before the controller first ran)."""
    return await session.get(ModelServiceState, 1)


async def touch_last_used(session_factory: async_sessionmaker) -> None:
    """Marks "the model was just used" (restarts the idle timer, FM-20)."""
    async with session_factory() as session:
        async with session.begin():
            await session.execute(text(
                "INSERT INTO model_service_state (id, last_used_at, updated_at) VALUES (1, now(), now()) "
                "ON CONFLICT (id) DO UPDATE SET last_used_at = now(), updated_at = now()"
            ))


def wait_reason(
    state: Optional[ModelServiceState], now: dt.datetime
) -> Tuple[str, Dict[str, Any]]:
    """``(reason, format args)`` for a ``waiting`` event while the model is not ready.

    ``controller_unresponsive`` when the controller has not reported for
    ``CONTROLLER_STALE_S`` (FM-31); otherwise the controller's view:
    starting (with an ETA), blocked by another team / a manual
    conversion, or cooling down after a failed start.
    """
    if state is None or state.controller_seen_at is None or \
            (now - state.controller_seen_at).total_seconds() > CONTROLLER_STALE_S:
        return "controller_unresponsive", {}
    if state.state == "blocked":
        if state.blocked_reason == "manual_converting":
            return "manual_converting", {}
        return "gpu_busy", {}
    if state.state == "failed" and state.cooldown_until is not None and now < state.cooldown_until:
        return "model_cooldown", {}
    eta = COLD_START_S
    if state.state == "starting" and state.requested_at is not None:
        eta = max(60, COLD_START_S - int((now - state.requested_at).total_seconds()))
    return "model_starting", {"eta_min": max(1, round(eta / 60))}


# ── controller: pure decision ─────────────────────────────────────────


@dataclass
class GpuVerdict:
    """Whether both GPUs are free of anyone else's memory."""

    free: bool
    reason: Optional[str] = None            # other_tenant | manual_converting | ollama_loaded | ours_busy
    snapshot: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class Observation:
    """What the controller saw on the host (one reconcile pass)."""

    container_running: bool
    ready: bool
    running_requests: int
    gpu: GpuVerdict
    eval_locked: bool
    demand: int                             # unfinished diagnoses
    last_finished_at: Optional[dt.datetime] = None


@dataclass
class StateView:
    """The fields of ``model_service_state`` the decision reads."""

    state: str = "stopped"
    started_by_us: bool = False
    requested_at: Optional[dt.datetime] = None
    ready_at: Optional[dt.datetime] = None
    last_used_at: Optional[dt.datetime] = None
    cooldown_until: Optional[dt.datetime] = None
    failure_reason: Optional[str] = None

    @classmethod
    def from_row(cls, row: Optional[ModelServiceState]) -> "StateView":
        if row is None:
            return cls()
        return cls(state=row.state, started_by_us=row.started_by_us, requested_at=row.requested_at,
                   ready_at=row.ready_at, last_used_at=row.last_used_at,
                   cooldown_until=row.cooldown_until, failure_reason=row.failure_reason)


@dataclass
class Decision:
    """``action`` (``start`` / ``stop`` / None) and the state columns to write."""

    action: Optional[str]
    fields: Dict[str, Any]
    note: str = ""


# Memory a card shows with no process on it (driver / persistence daemon);
# below this, unaccounted memory is not counted as another tenant's.
_DRIVER_NOISE_MIB = 512


def classify_gpus(
    gpus: Sequence[Tuple[int, str, int]],
    apps: Sequence[Tuple[str, int, int]],
    procs: Dict[int, Optional[Tuple[str, str]]],
    *,
    our_user: str,
    free_mib: int,
    manual_converting: bool,
) -> GpuVerdict:
    """Who holds GPU memory, and may we start vLLM (FM-50 / FM-45)?

    Args:
        gpus: ``(index, uuid, used MiB)`` per card.
        apps: ``(gpu uuid, pid, used MiB)`` per compute process.
        procs: pid → ``(user, command line)``; None when unknown.
        our_user: The account the controller runs as.
        free_mib: A card whose memory held by anything but our vLLM adds
            up to less than this counts as free.
        manual_converting: A manual conversion is running (our MinerU).

    A process of another user — or one whose owner cannot be read — is
    ``other_tenant`` (never grab a card we cannot account for); our own
    vLLM does not block; our Ollama or other processes do.  Memory on a
    card that no listed process accounts for counts as another tenant's.
    The line is per CARD, not per process: nine 0.7–0.9 GB processes of
    another user are a busy card (PROD-11 server finding — the per-process
    rule started vLLM on top of them).
    """
    index_of = {u: i for i, u, _ in gpus}
    snapshot: List[Dict[str, Any]] = []
    blockers: List[str] = []
    listed: Dict[str, int] = {}
    load: Dict[str, Dict[str, int]] = {}      # card → kind → MiB (all but our vLLM)
    for gpu_uuid, pid, used in apps:
        info = procs.get(pid)
        if info is None:
            kind = "other_tenant"
        else:
            user, args = info
            low = args.lower()
            if user != our_user:
                kind = "other_tenant"
            elif "vllm" in low:
                kind = "vllm"
            elif "ollama" in low:
                kind = "ollama"
            else:
                kind = "ours"
        listed[gpu_uuid] = listed.get(gpu_uuid, 0) + used
        snapshot.append({"gpu": index_of.get(gpu_uuid), "pid": pid, "mib": used, "kind": kind})
        if kind != "vllm":
            per = load.setdefault(gpu_uuid, {})
            per[kind] = per.get(kind, 0) + used
    for idx, gpu_uuid, used in gpus:
        extra = used - listed.get(gpu_uuid, 0)
        if extra >= _DRIVER_NOISE_MIB:
            per = load.setdefault(gpu_uuid, {})
            per["other_tenant"] = per.get("other_tenant", 0) + extra
            snapshot.append({"gpu": idx, "pid": None, "mib": extra, "kind": "unaccounted"})
    for per in load.values():
        if sum(per.values()) >= free_mib:
            blockers.extend(kind for kind, mib in per.items() if mib > 0)
    if not blockers:
        return GpuVerdict(free=True, snapshot=snapshot)
    if "other_tenant" in blockers:
        reason = "other_tenant"
    elif "ours" in blockers and manual_converting:
        reason = "manual_converting"
    elif "ollama" in blockers:
        reason = "ollama_loaded"
    else:
        reason = "ours_busy"
    return GpuVerdict(free=False, reason=reason, snapshot=snapshot)


def decide(obs: Observation, st: StateView, settings: Any, now: dt.datetime) -> Decision:
    """The controller's rules — pure, so every branch is unit-tested.

    * ready → state ready; stop only if WE started it, nothing unfinished,
      idle ≥ ``llm_idle_stop_s`` since the last use / readiness, no eval
      lock, no request in flight (FM-20 / FM-26);
    * loading → state starting; our own start past ``llm_start_timeout_s``
      → stop it, state failed + cooldown (FM-24);
    * not running → our start exited → failed + cooldown; demand and no
      cooldown and both cards free → start (once: the next pass sees it
      loading, FM-20); demand but cards busy → blocked with the reason
      (never grab, D1); no demand → stopped.
    """
    upd: Dict[str, Any] = {"controller_seen_at": now}
    cooldown = dt.timedelta(seconds=settings.llm_start_cooldown_s)
    if obs.container_running and obs.ready:
        upd.update(state="ready", blocked_reason=None)
        if st.state != "ready" or st.ready_at is None:
            upd["ready_at"] = now
        if obs.demand == 0 and st.started_by_us:
            marks = [m for m in (st.last_used_at, obs.last_finished_at, st.ready_at or now) if m is not None]
            idle_s = (now - max(marks)).total_seconds()
            if idle_s >= settings.llm_idle_stop_s:
                if obs.eval_locked or obs.running_requests > 0:
                    return Decision(None, upd, "idle but an eval / request holds it")
                upd.update(state="stopped", started_by_us=False, ready_at=None)
                return Decision("stop", upd, f"idle {int(idle_s)} s")
        return Decision(None, upd, "ready")
    if obs.container_running:
        if st.started_by_us and st.requested_at is not None and \
                (now - st.requested_at).total_seconds() > settings.llm_start_timeout_s:
            upd.update(state="failed", failed_at=now, failure_reason="start timed out",
                       cooldown_until=now + cooldown, started_by_us=False, ready_at=None)
            return Decision("stop", upd, "start timed out")
        upd.update(state="starting", blocked_reason=None)
        return Decision(None, upd, "loading")
    # container not running
    if st.state == "starting" and st.started_by_us:
        upd.update(state="failed", failed_at=now, failure_reason="vLLM exited while starting",
                   cooldown_until=now + cooldown, started_by_us=False, ready_at=None)
        return Decision("stop", upd, "exited while starting")
    if st.state == "ready" and st.started_by_us:
        # PROD-11 server finding: something outside the controller stopped the
        # vLLM we started.  Do not fight it: fail + cool down; waiting runs end
        # with ``model_stopped`` instead of re-starting it every minute.
        upd.update(state="failed", failed_at=now, failure_reason=STOPPED_EXTERNALLY,
                   cooldown_until=now + cooldown, started_by_us=False, ready_at=None)
        return Decision(None, upd, "stopped externally")
    upd.update(started_by_us=False, ready_at=None)
    if obs.demand > 0:
        if st.cooldown_until is not None and now < st.cooldown_until:
            upd["state"] = "failed"
            return Decision(None, upd, "cooling down")
        if not obs.gpu.free:
            upd.update(state="blocked", blocked_reason=obs.gpu.reason, gpu_snapshot=obs.gpu.snapshot)
            return Decision(None, upd, f"blocked: {obs.gpu.reason}")
        upd.update(state="starting", started_by_us=True, requested_at=now, blocked_reason=None,
                   failure_reason=None, cooldown_until=None, gpu_snapshot=obs.gpu.snapshot)
        return Decision("start", upd, "demand, GPUs free")
    upd.update(state="stopped", blocked_reason=None)
    return Decision(None, upd, "no demand")


# ── controller: host I/O ──────────────────────────────────────────────


class HostIO:
    """The controller's view of the host (replaced by a fake in tests)."""

    def __init__(self, settings: Any) -> None:
        self.settings = settings

    async def _run(self, argv: List[str], timeout: float) -> Tuple[int, str, str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        except (FileNotFoundError, PermissionError) as exc:
            return 127, "", str(exc)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except (TimeoutError, asyncio.TimeoutError):
            proc.kill()
            return 124, "", f"timed out after {timeout:.0f} s"
        return proc.returncode or 0, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")

    async def container_running(self) -> bool:
        rc, out, _ = await self._run(
            ["podman", "container", "inspect", VLLM_CONTAINER, "--format", "{{.State.Running}}"], 30)
        return rc == 0 and out.strip().lower() == "true"

    async def ready(self) -> bool:
        return await model_ready(self.settings)

    async def running_requests(self) -> int:
        base = re.sub(r"/v1/?$", "", self.settings.llm_base_url.rstrip("/"))
        try:
            async with httpx.AsyncClient(timeout=5.0) as c:
                resp = await c.get(base + "/metrics")
            total = 0.0
            for line in resp.text.splitlines():
                if line.startswith("vllm:num_requests_running"):
                    total += float(line.rsplit(" ", 1)[-1])
            return int(total)
        except (httpx.HTTPError, ValueError):
            return 0

    async def gpu_rows(self) -> List[Tuple[int, str, int]]:
        rc, out, _ = await self._run(["nvidia-smi", "--query-gpu=index,uuid,memory.used",
                                      "--format=csv,noheader,nounits"], 30)
        rows = []
        for line in out.splitlines() if rc == 0 else []:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) == 3 and parts[0].isdigit():
                rows.append((int(parts[0]), parts[1], int(float(parts[2]))))
        return rows

    async def app_rows(self) -> List[Tuple[str, int, int]]:
        rc, out, _ = await self._run(["nvidia-smi", "--query-compute-apps=gpu_uuid,pid,used_memory",
                                      "--format=csv,noheader,nounits"], 30)
        rows = []
        for line in out.splitlines() if rc == 0 else []:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) == 3 and parts[1].isdigit():
                try:
                    rows.append((parts[0], int(parts[1]), int(float(parts[2]))))
                except ValueError:
                    continue
        return rows

    async def proc_info(self, pid: int) -> Optional[Tuple[str, str]]:
        rc, out, _ = await self._run(["ps", "-o", "user=,args=", "-p", str(pid)], 10)
        line = out.strip()
        if rc != 0 or not line:
            return None
        user, _, args = line.partition(" ")
        return user.strip(), args.strip()

    def our_user(self) -> str:
        return getpass.getuser()

    async def eval_locked(self) -> bool:
        """An eval holds vLLM only while the container named in the lock runs.

        ``run_golden_eval.sh`` never deletes the lock (the eval container is
        detached with ``--rm``) and itself treats a lock naming a stopped
        container as stale.  PROD-11 server finding: checking only that the
        file exists kept vLLM resident forever after the first eval.
        """
        path = Path(os.path.expanduser(self.settings.eval_lock_path))
        try:
            name = path.read_text(encoding="utf-8").strip()
        except OSError:
            return False
        if not name:
            return False
        rc, out, _ = await self._run(
            ["podman", "container", "inspect", name, "--format", "{{.State.Running}}"], 30)
        return rc == 0 and out.strip().lower() == "true"

    def ctl_path(self) -> str:
        if self.settings.vllm_ctl_path:
            return self.settings.vllm_ctl_path
        return str(Path(self.settings.repo_dir or ".") / "infra" / "vllm_ctl.sh")

    def ctl_argv(self, action: str) -> List[str]:
        """The command line for ``vllm_ctl.sh start|stop`` (FM-40: nothing else).

        ``start`` runs inside its own transient systemd scope: podman's conmon
        (the process that keeps the container alive) would otherwise sit in
        this worker service's cgroup, and every "restart the GPU worker after
        a deploy" would take vLLM down with it (PROD-11 server finding).
        """
        if action not in ("start", "stop"):
            raise ValueError(action)
        base = ["bash", self.ctl_path(), action]
        if action == "start" and getattr(self.settings, "llm_ctl_scope", True):
            return ["systemd-run", "--user", "--scope", "--quiet", "--collect",
                    f"--unit=stf-llm-start-{os.getpid()}-{int(dt.datetime.now().timestamp())}"] + base
        return base

    async def run_ctl(self, action: str) -> Tuple[int, str]:
        """``vllm_ctl.sh start|stop`` — the only commands the controller runs (FM-40)."""
        rc, out, err = await self._run(self.ctl_argv(action), 300)
        return rc, (err or out)[-500:]


async def observe(io: HostIO, settings: Any, *, demand: int, manual_converting: bool,
                  last_finished_at: Optional[dt.datetime]) -> Observation:
    """One look at the host."""
    running = await io.container_running()
    ready = await io.ready() if running else False
    gpus = await io.gpu_rows()
    apps = await io.app_rows()
    procs = {pid: await io.proc_info(pid) for _, pid, _ in apps}
    verdict = classify_gpus(gpus, apps, procs, our_user=io.our_user(),
                            free_mib=settings.llm_gpu_free_mib, manual_converting=manual_converting)
    return Observation(
        container_running=running, ready=ready,
        running_requests=await io.running_requests() if ready else 0,
        gpu=verdict, eval_locked=await io.eval_locked(), demand=demand,
        last_finished_at=last_finished_at,
    )


async def _locked_state(session: AsyncSession) -> ModelServiceState:
    await session.execute(text(
        "INSERT INTO model_service_state (id) VALUES (1) ON CONFLICT (id) DO NOTHING"))
    return (await session.execute(
        select(ModelServiceState).where(ModelServiceState.id == 1).with_for_update()
    )).scalar_one()


async def reconcile(session_factory: async_sessionmaker, settings: Any, io: HostIO) -> Decision:
    """One controller pass: observe → decide → write state → run the command.

    The state row is written (and committed) BEFORE the command runs, so a
    concurrent reader never sees "stopped" while a start is under way; a
    failed start is written back in a second short transaction with a
    cooldown (FM-24).  Callers serialise passes (task lock ``llm-control``).
    """
    from stf_v3.knowledge.models import Manual   # read-only: our own MinerU busy?
    from stf_v3.diagnosis.agent.model import PROFILE_QWEN_VLLM, select_profile

    async with session_factory() as session:
        async with session.begin():
            row = await _locked_state(session)
            now = (await session.execute(select(func.now()))).scalar_one()
            demand = int((await session.execute(
                select(func.count()).select_from(DiagnosisConversation)
                .where(DiagnosisConversation.status.in_(ACTIVE_STATUSES))
            )).scalar_one())
            converting = int((await session.execute(
                select(func.count()).select_from(Manual).where(Manual.status == "converting")
            )).scalar_one()) > 0
            last_finished = (await session.execute(
                select(func.max(DiagnosisConversation.finished_at))
            )).scalar_one()
            obs = await observe(io, settings, demand=demand, manual_converting=converting,
                                last_finished_at=last_finished)
            view = StateView.from_row(row)
            if not settings.llm_autostart or select_profile(settings) != PROFILE_QWEN_VLLM:
                decision = Decision(None, {"controller_seen_at": now,
                                           "state": "ready" if obs.ready else "stopped"},
                                    "autostart off / not the vLLM profile")
            else:
                decision = decide(obs, view, settings, now)
            for key, value in decision.fields.items():
                setattr(row, key, value)
            row.updated_at = now
    if decision.action:
        if decision.action == "start":
            log.info("llm.start", demand=demand, gpu_snapshot=decision.fields.get("gpu_snapshot"))
        rc, tail = await io.run_ctl(decision.action)
        log.info("llm.ctl", action=decision.action, rc=rc, note=decision.note)
        if rc != 0 and decision.action == "start":
            async with session_factory() as session:
                async with session.begin():
                    row = await _locked_state(session)
                    now = (await session.execute(select(func.now()))).scalar_one()
                    row.state = "failed"
                    row.failed_at = now
                    row.failure_reason = f"start command failed (rc={rc})"
                    row.cooldown_until = now + dt.timedelta(seconds=settings.llm_start_cooldown_s)
                    row.started_by_us = False
                    row.updated_at = now
            log.warning("llm.start_failed", rc=rc, output_tail=tail[-200:])
    else:
        log.info("llm.reconcile", note=decision.note, state=decision.fields.get("state"))
    return decision
