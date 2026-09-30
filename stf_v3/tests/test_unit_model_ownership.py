"""#255: the on-demand controller tells whose vLLM is whose by ownership
(account + container / service), never by the command line (T-1 … T-8).

2026-09-27 the Gemma competition ran its OWN vLLM (and a sandbox
container) on the same Unix account; "our account + 'vllm' in the command
line" made the controller think the cards were free, start V3's vLLM,
fail for lack of memory and cool down.  Every process list here is made
up (FM-23): fake users, fake paths, fake container ids.

Author: Xiangzhu Yan
"""

from __future__ import annotations

import pathlib
import re
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pytest
import structlog
import yaml

from stf_v3.diagnosis import model_service as ms
from stf_v3.diagnosis.texts import WAIT_REASONS, wait_text
from tests.conftest import requires_db
from tests.test_unit_model_service import (
    CONTAINERS, NOW, OLLAMA_ID, SANDBOX_ID, SSH, VLLM_ID, WORKER_ID, WORKER_SVC, FakeIO, _gpus, own, pod,
)

REPO = pathlib.Path(__file__).resolve().parents[2]


def _verdict(procs: Dict[int, Any], apps: List[Tuple[str, int, int]], *, converting: bool = False,
             containers: Optional[Dict[str, str]] = CONTAINERS, free_mib: int = 6000) -> ms.GpuVerdict:
    used = {"GPU-a": 0, "GPU-b": 0}
    for g, _, u in apps:
        used[g] += u
    return ms.classify_gpus(_gpus(used["GPU-a"], used["GPU-b"]), apps, procs, our_user="talon",
                            free_mib=free_mib, manual_converting=converting, containers=containers)


# ── T-1 who is who (FM-2 / 3 / 4 / 9 / 23 / 30) ────────────────────


@pytest.mark.parametrize("owner,kind", [
    (own("talon", pod(VLLM_ID)), "vllm"),                    # started by the controller (own scope)
    (own("talon", "0::/user.slice/user-1006.slice/session-9.scope/libpod-" + VLLM_ID + ".scope\n"),
     "vllm"),                                                # started by hand from SSH (vllm_ctl.sh)
    (own("talon", SSH), "same_account"),                     # the Gemma vLLM from an SSH session
    (own("talon", pod(SANDBOX_ID)), "same_account"),          # another project's container
    (own("talon", WORKER_SVC), "ours"),                      # MinerU under the host GPU worker
    (own("talon", pod(OLLAMA_ID)), "ollama"),                # V1/V2 Ollama
    (own("talon", pod(WORKER_ID)), "ours"),                  # a stf-v3-* container
    (own("martin", pod(VLLM_ID)), "other_tenant"),           # another account, whatever its cgroup
    (None, "other_tenant"),                                   # owner unknown
])
def test_every_owner_is_classified_by_account_and_container(owner: Optional[ms.ProcOwner], kind: str) -> None:
    """Only a process inside the ``stf-vllm`` container is V3's vLLM — either
    way it was started; Gemma's vLLM in an SSH session or another container
    is "another project of this account"; a container check comes before the
    GPU-worker check (FM-3)."""
    assert ms.process_kind(owner, "talon", CONTAINERS)[0] == kind


def test_the_gemma_vllm_blocks_and_v3s_own_does_not() -> None:
    """The server case: Gemma's vLLM (33 GB/card, SSH session) → blocked with
    ``same_account``; the same memory in the ``stf-vllm`` container → free."""
    gemma = {1: own("talon", SSH), 2: own("talon", SSH)}
    apps = [("GPU-a", 1, 32738), ("GPU-b", 2, 32738)]
    v = _verdict(gemma, apps)
    assert not v.free and v.reason == "same_account"
    ours = {1: own("talon", pod(VLLM_ID)), 2: own("talon", pod(VLLM_ID))}
    assert _verdict(ours, apps).free


def test_no_command_line_test_is_left_in_the_controller() -> None:
    """FM-9: one classifier; nothing decides by 'vllm' / 'ollama' in a command line."""
    src = pathlib.Path(ms.__file__).read_text(encoding="utf-8")
    assert not re.search(r"""["']vllm["']\s+in\s+""", src)
    assert not re.search(r"""["']ollama["']\s+in\s+""", src)
    assert src.count("def process_kind(") == 1 and "process_kind(procs.get(pid)" in src


# ── T-2 robustness (FM-5 / 6 / 7 / 14) ─────────────────────────────


def test_container_ids_are_read_again_every_pass() -> None:
    """FM-5: after a restart stf-vllm has a new id; the new map recognises it."""
    new_id = "e" * 64
    owner = own("talon", pod(new_id))
    assert ms.process_kind(owner, "talon", CONTAINERS)[0] == "same_account"        # stale map
    assert ms.process_kind(owner, "talon", {new_id: "stf-vllm"})[0] == "vllm"      # fresh map


def test_vanished_or_unreadable_processes_block_and_never_raise() -> None:
    """FM-6 / FM-7: gone (None) → other tenant; our process with no readable
    cgroup → another project of this account; both block, nothing raises."""
    apps = [("GPU-a", 1, 9000), ("GPU-b", 2, 9000)]
    v = _verdict({1: None, 2: own("talon", "")}, apps)
    assert not v.free and {r["kind"] for r in v.snapshot} == {"other_tenant", "same_account"}


def test_a_failed_container_listing_blocks_our_containerised_processes() -> None:
    """FM-14: podman listing failed (None) → our container processes cannot be
    identified → they block (never grab a card we cannot account for)."""
    v = _verdict({1: own("talon", pod(VLLM_ID))}, [("GPU-a", 1, 36000)], containers=None)
    assert not v.free and v.reason == "same_account"


async def test_hostio_reads_owner_and_cgroup_and_containers_with_timeouts(tmp_path: pathlib.Path,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    """FM-7 / FM-13 / FM-29: only ``ps -o user=`` and the world-readable
    cgroup file; the container listing is read-only; every call has a
    timeout ≤ 10 s; a failed listing returns None."""
    calls: List[Tuple[List[str], float]] = []
    io = ms.HostIO(SimpleNamespace())

    async def fake_run(argv: List[str], timeout: float) -> Tuple[int, str, str]:
        calls.append((argv, timeout))
        if argv[0] == "ps":
            return 0, "talon\n", ""
        if argv[:2] == ["podman", "ps"]:
            return 0, f"{VLLM_ID} stf-vllm\n{SANDBOX_ID} sleepy_sandbox\n", ""
        return 1, "", "?"

    monkeypatch.setattr(io, "_run", fake_run)
    owner = await io.proc_info(424242)
    assert owner is not None and owner.user == "talon"          # cgroup "" when /proc is not there
    assert await io.container_ids() == {VLLM_ID: "stf-vllm", SANDBOX_ID: "sleepy_sandbox"}
    assert all(t <= 10 for _, t in calls)
    assert [c[0][:3] for c in calls] == [["ps", "-o", "user="], ["podman", "ps", "--no-trunc"]]

    async def failing(argv: List[str], timeout: float) -> Tuple[int, str, str]:
        return 125, "", "storage locked"

    monkeypatch.setattr(io, "_run", failing)
    assert await io.container_ids() is None and await io.proc_info(1) is None


# ── T-3 which reason, which words (FM-10 / 21 / 24 / 32) ───────────


def test_priority_other_team_first_then_this_account_then_ours() -> None:
    """FM-21: another team + Gemma + MinerU on the cards → 'other_tenant'
    (what we cannot control ourselves); Gemma + MinerU → 'same_account'."""
    apps = [("GPU-a", 1, 9000), ("GPU-a", 2, 9000), ("GPU-b", 3, 30000)]
    all3 = {1: own("martin"), 2: own("talon", SSH), 3: own("talon", WORKER_SVC)}
    assert _verdict(all3, apps, converting=True).reason == "other_tenant"
    two = {1: own("talon", SSH), 2: own("talon", SSH), 3: own("talon", WORKER_SVC)}
    assert _verdict(two, apps, converting=True).reason == "same_account"
    assert _verdict({3: own("talon", WORKER_SVC)}, [("GPU-b", 3, 30000)], converting=True).reason \
        == "manual_converting"
    assert ms.BLOCK_PRIORITY == ("other_tenant", "same_account", "manual_converting", "ollama_loaded", "ours_busy")


@pytest.mark.parametrize("blocked,wait", [
    ("other_tenant", "gpu_busy"), ("same_account", "gpu_busy_internal"),
    ("manual_converting", "manual_converting"), ("ollama_loaded", "gpu_old_model"),
    ("ours_busy", "gpu_project_task"), ("something_new", "gpu_busy"),
])
def test_every_blocked_reason_maps_to_a_worded_wait_reason(blocked: str, wait: str) -> None:
    """FM-32 / FM-10: each blocked reason has its own waiting reason, worded
    in all three languages; old codes keep their names (FM-19)."""
    row = SimpleNamespace(state="blocked", blocked_reason=blocked, controller_seen_at=NOW,
                          cooldown_until=None, requested_at=None)
    assert ms.wait_reason(row, NOW)[0] == wait
    assert set(WAIT_REASONS[wait]) == {"zh-TW", "zh-CN", "en"}


def test_the_internal_wording_never_says_this_account() -> None:
    """FM-24: a workshop user would read 'this account' as their workshop's;
    the text says 'another internal task on the server'."""
    t = WAIT_REASONS["gpu_busy_internal"]
    assert t["zh-TW"] == "伺服器上的另一項內部任務正在使用顯示卡，排隊等待中"
    assert t["zh-CN"] == "服务器上的另一项内部任务正在使用显卡，排队等待中"
    assert all("帳號" not in v and "账号" not in v and "account" not in v for v in t.values())


def test_an_unknown_wait_reason_gets_a_generic_sentence() -> None:
    """FM-10: never the raw code, never an error."""
    assert wait_text("from_the_future", "zh-CN") == "排队等待中"
    assert wait_text("from_the_future", "en") == "Waiting"


# ── T-6 names match the deployment files (FM-18) ──────────────────


def test_container_and_service_names_match_the_deployment_files() -> None:
    """The names the controller relies on are the ones the files define."""
    infra = REPO / "infra"
    if not infra.is_dir():
        pytest.skip("infra/ not present (portable copy)")
    names = set()
    for f in ("docker-compose.vllm.yml", "docker-compose.yml", "docker-compose.v3.yml"):
        doc = yaml.safe_load((infra / f).read_text(encoding="utf-8"))
        names |= {svc.get("container_name") for svc in (doc.get("services") or {}).values()}
    assert ms.VLLM_CONTAINER in names and ms.OLLAMA_CONTAINER in names
    assert any(str(n).startswith(ms.V3_CONTAINER_PREFIX) for n in names if n)
    assert (REPO / "stf_v3" / "gpu_worker" / ms.GPU_WORKER_SERVICE).is_file()


def test_blocked_reason_codes_fit_the_state_column() -> None:
    """FM-1 / FM-31: the column holds 30 characters."""
    assert all(len(r) <= 30 for r in ms.BLOCK_PRIORITY)


# ── T-4 / T-7 the state row, health and the log (FM-1 / 16 / 20 / 21 / 23) ──


@requires_db
async def test_blocked_by_gemma_is_written_shown_and_logged(client, workshop_with_codes, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A waiting diagnosis, the Gemma vLLM on both cards: no start command;
    the state row says ``same_account``; /v3/health shows it and every kind
    holding memory; one log line lists who (container / service labels),
    never a user name or a command line."""
    from stf_v3.db import SessionLocal
    from stf_v3.settings import settings
    from tests.diagnosis_helpers import install_fake_queue, seed

    install_fake_queue(monkeypatch)
    monkeypatch.setattr(settings, "manual_storage_path", str(tmp_path))     # health reads its disk
    wid, codes = workshop_with_codes
    s = await seed(client, wid, codes)
    r = await client.post(f"/v3/vehicles/{s.vehicle_id}/diagnose", headers=s.headers,
                          json={"obd_log_id": str(s.log_id)})
    assert r.status_code == 202
    io = FakeIO(settings, apps=[("GPU-a", 1, 32738), ("GPU-b", 2, 32738), ("GPU-b", 3, 3000)],
                procs={1: own("talon", SSH), 2: own("talon", SSH), 3: own("martin")})
    with structlog.testing.capture_logs() as logs:
        d = await ms.reconcile(SessionLocal, settings, io)
    assert d.action is None and io.commands == []
    async with SessionLocal() as session:
        row = await ms.get_state(session)
        assert row.state == "blocked" and row.blocked_reason == "other_tenant"   # another team outranks
    health = (await client.get("/v3/health")).json()["model_service"]
    assert health["blocked_reason"] == "other_tenant"
    assert health["blocked_by"] == ["other_tenant", "same_account"]
    blocked = [e for e in logs if e.get("event") == "llm.blocked"]
    assert len(blocked) == 1
    text = repr(blocked[0])
    assert "martin" not in text and "session-" not in text and "gemma" not in text.lower()
    assert "outside the V3 services" in text and "another user" in text
