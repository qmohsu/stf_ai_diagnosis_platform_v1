"""#253 T-8 / T-11: the GPU contention sampler with fake host commands.

The sampler runs on the PolyU host beside a golden eval; here every host
call (``nvidia-smi``, ``podman``, the vLLM metrics page, ``/proc``) is a
fake, so the tests need no GPU.

Author: Xiangzhu Yan
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
from typing import Any, Dict, List, Optional, Sequence

from stf_v3.evals import gate

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "gpu_contention.py"
_spec = importlib.util.spec_from_file_location("gpu_contention", _SCRIPT)
assert _spec and _spec.loader
gc: Any = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gc)

ME, MARTIN = 1006, 1003
GPUS = "0, GPU-a, 34000, 46068, 95\n1, GPU-b, 30000, 46068, 40\n"
BASE = "20260927T010203Z_0123abcd_gate_local_think-off"
# pid → (uid, command line): our vLLM on both cards, Martin's training on GPU 0
POD = "0::/user.slice/user-1006.slice/user@1006.service/user.slice/libpod-9bb1.scope"
WORKER = "0::/user.slice/user-1006.slice/user@1006.service/app.slice/stf-v3-gpu-worker.service"
SSH = "0::/user.slice/user-1006.slice/session-479435.scope"
# pid → (uid, command line, cgroup): our vLLM on both cards, Martin's
# training on GPU 0, our Ollama and MinerU, and this account's own
# non-V3 vLLM (the Gemma competition) started from an SSH session.
PROCS: Dict[int, tuple] = {
    11: (ME, "VLLM::Worker_TP0", POD), 12: (ME, "VLLM::Worker_TP1", POD),
    21: (MARTIN, "/home/martin/venv/bin/python pl_b2.py train secret-project --api-key=zzfake987", ""),
    31: (ME, "/usr/bin/ollama runner", POD), 41: (ME, "mineru -p manual.pdf", WORKER),
    51: (ME, "/home/talon/gemma4_agent_comp/.venv_vllm/bin/python -m vllm.entrypoints.openai.api_server", SSH),
}


class FakeHost:
    """Scripted host: a list of (gpu csv, apps csv, pmon text) per sample."""

    def __init__(self, samples: List[tuple], alive_for: int, hang_at: Optional[int] = None) -> None:
        self.samples = samples
        self.alive_for = alive_for
        self.hang_at = hang_at
        self.i = 0
        self.calls: List[List[str]] = []

    def run(self, argv: Sequence[str], timeout: float) -> Optional[str]:
        self.calls.append(list(argv))
        assert timeout <= gc.CALL_TIMEOUT_S           # FM-11: every call has a timeout
        if argv[:3] == ["podman", "container", "exists"]:
            self.i += 1
            return "" if self.i < self.alive_for else None
        k = min(self.i, len(self.samples) - 1)
        gpus, apps, pmon = self.samples[k]
        if "--query-gpu=index,uuid,memory.used,memory.total,utilization.gpu" in argv:
            return None if self.hang_at == self.i else gpus
        if "--query-compute-apps=gpu_uuid,pid,used_memory" in argv:
            return apps
        if "pmon" in argv:
            return pmon
        raise AssertionError(argv)


def _classify(pid: int) -> str:
    uid, cmd, cg = PROCS.get(pid, (None, "", ""))
    return gc.classify(pid, ME, owner=lambda p: uid, cmdline=lambda p: cmd.lower(), cgroup=lambda p: cg)


def test_this_accounts_processes_outside_v3_count_as_other_workloads() -> None:
    """FM-4 (server finding 2026-09-27): the Gemma competition's vLLM on the
    same account is NOT our vLLM; MinerU under the GPU worker is V3."""
    assert _classify(11) == "vllm" and _classify(31) == "ollama" and _classify(41) == "project_other"
    assert _classify(51) == "others" and _classify(21) == "others" and _classify(999) == "others"


def _run(host: FakeHost, tmp: pathlib.Path, before: float = 3, after: float = 3) -> Dict[str, Any]:
    (tmp / f"{BASE}.slim.json").write_text("{}", encoding="utf-8")
    vals = iter([before, after])
    clock = iter(range(0, 10_000, 30))
    path = gc.sample_run("stf-v3-eval-20260927T010200Z", tmp, interval=30, concurrency=3,
                         metrics_url="http://x/metrics", max_s=3600, run=host.run, sleep=lambda s: None,
                         clock=lambda: float(next(clock)), my_uid=ME, classify_fn=_classify,
                         metrics=lambda url: next(vals))
    assert path.name == f"{BASE}{gc.SUFFIX}"
    return json.loads(path.read_text(encoding="utf-8"))


APPS_BUSY = ("GPU-a, 11, 28400\nGPU-a, 21, 5700\nGPU-b, 12, 28400\n"
             "GPU-b, 31, 1500\nGPU-b, 41, 0\n")
PMON_BUSY = "# gpu pid type sm mem\n0 11 C 40 10\n0 21 C 55 20\n1 12 C 30 5\n"
APPS_QUIET = "GPU-a, 11, 28400\nGPU-b, 12, 28400\n"
PMON_QUIET = "# gpu pid type sm mem\n0 11 C 40 10\n1 12 C 30 5\n"
GPUS_QUIET = "0, GPU-a, 28550, 46068, 40\n1, GPU-b, 28550, 46068, 30\n"


def test_the_record_holds_numbers_only_and_passes_the_gate_whitelist(tmp_path: pathlib.Path) -> None:
    """T-8 / FM-18 / FM-19 / FM-4 / FM-21: Martin's name, his command line and
    the key in it never reach the file; his memory / compute count as other
    users, Ollama is its own group, our MinerU is "project other"."""
    rec = _run(FakeHost([(GPUS, APPS_BUSY, PMON_BUSY)], alive_for=3), tmp_path)
    text = json.dumps(rec)
    for leak in ("martin", "pl_b2", "secret", "api-key", "zzfake987", "vllm::", "ollama runner", "mineru"):
        assert leak not in text.lower(), leak
    assert gate.validate_contention(rec) == []
    g0, g1 = rec["gpus"]
    assert g0["others_mib_max"] == 5700 and g0["others_sm_pct_max"] == 55 and g0["vllm_mib_max"] == 28400
    assert g1["ollama_mib_max"] == 1500 and g1["others_mib_max"] == 0
    warns = gate.contention_warnings(rec, BASE)
    assert any("GPU 0: other workloads held up to 5700" in w for w in warns)
    assert any("GPU 1: Ollama held 1500" in w for w in warns)


def test_a_hung_gpu_query_is_skipped_and_the_run_goes_on(tmp_path: pathlib.Path) -> None:
    """T-11 ① / FM-11: a query that fails / times out is counted, not fatal."""
    host = FakeHost([(GPUS_QUIET, APPS_QUIET, PMON_QUIET)], alive_for=4, hang_at=1)
    rec = _run(host, tmp_path)
    assert rec["sample_errors"] == 1 and rec["samples"] == 3


def test_it_follows_the_container_and_catches_a_mid_run_tenant(tmp_path: pathlib.Path) -> None:
    """T-11 ② ③ / FM-14 / FM-33: quiet → busy → quiet; the container ends
    after the 4th sample → the record is written then, and its maximum
    shows the tenant that came and went in the middle."""
    host = FakeHost([(GPUS_QUIET, APPS_QUIET, PMON_QUIET), (GPUS_QUIET, APPS_QUIET, PMON_QUIET),
                     (GPUS, APPS_BUSY, PMON_BUSY), (GPUS_QUIET, APPS_QUIET, PMON_QUIET)], alive_for=4)
    rec = _run(host, tmp_path)
    assert rec["samples"] == 4
    assert rec["gpus"][0]["others_mib_max"] == 5700 and rec["gpus"][0]["others_mib_mean"] < 5700
    exists_calls = [c for c in host.calls if c[:3] == ["podman", "container", "exists"]]
    assert len(exists_calls) == 4                    # stopped as soon as the container was gone


def test_preemption_delta_and_a_vllm_restart(tmp_path: pathlib.Path) -> None:
    """FM-10: before 3 → after 7 = 4 preemptions; after < before = restart."""
    quiet = [(GPUS_QUIET, APPS_QUIET, PMON_QUIET)]
    rec = _run(FakeHost(quiet, alive_for=2), tmp_path, before=3, after=7)
    assert rec["preemption_delta"] == 4 and rec["vllm_restarted"] is False
    rec = _run(FakeHost(quiet, alive_for=2), tmp_path, before=9, after=0)
    assert rec["preemption_delta"] is None and rec["vllm_restarted"] is True


def test_unaccounted_memory_counts_as_another_tenant() -> None:
    """Memory no listed process explains (above the driver's ~0.5 GB) is not ours."""
    s = gc.take_sample(FakeHost([("0, GPU-a, 31000, 46068, 50\n", "GPU-a, 11, 28400\n", "")], 99).run,
                       ME, _classify)
    assert s is not None and s[0]["others"] == 2600


def test_without_a_scorecard_the_record_is_still_written(tmp_path: pathlib.Path) -> None:
    """A run that died before writing a scorecard still leaves contention.json."""
    vals = iter([0.0, 0.0])
    clock = iter(range(0, 10_000, 30))
    host = FakeHost([(GPUS_QUIET, APPS_QUIET, PMON_QUIET)], alive_for=2)
    path = gc.sample_run("stf-v3-eval-20260927T010200Z", tmp_path, interval=30, concurrency=None,
                         metrics_url="x", max_s=3600, run=host.run, sleep=lambda s: None,
                         clock=lambda: float(next(clock)), my_uid=ME, classify_fn=_classify,
                         metrics=lambda url: next(vals))
    assert path.name == "contention.json" and json.loads(path.read_text(encoding="utf-8"))["scorecard"] is None


def test_the_launcher_starts_the_sampler_for_local_runs_only() -> None:
    """FM-33: the sampler is its own transient user service following the
    container (so --wait or not makes no difference); cloud runs skip it."""
    sh = (_SCRIPT.parent / "run_golden_eval.sh").read_text(encoding="utf-8")
    assert "systemd-run --user --collect" in sh and "gpu_contention.py\" sample" in sh
    assert 'if [ $CLOUD -eq 0 ]; then' in sh.split("gpu_contention.py")[0].rsplit("# 6.", 1)[1]
