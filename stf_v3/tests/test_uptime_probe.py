"""2026-11-21 outage: the outside probe that raises the alert issue.

The fetcher is scripted, so the tests need no network.

Author: Xiangzhu Yan
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import pathlib
import sys
from typing import Any, Dict, List, Optional, Tuple

_SCRIPT = (pathlib.Path(__file__).resolve().parents[1] / "scripts"
           / "uptime_probe.py")
_spec = importlib.util.spec_from_file_location("stf_uptime_probe", _SCRIPT)
assert _spec and _spec.loader
up: Any = importlib.util.module_from_spec(_spec)
sys.modules["stf_uptime_probe"] = up
_spec.loader.exec_module(up)

BASE = "https://site.test"


def _health(**changes: Any) -> Tuple[int, bytes]:
    doc: Dict[str, Any] = {
        "status": "ok", "db": "ok",
        "gpu_worker": {"alive": True, "age_s": 12},
        "model_service": {"state": "stopped", "controller_seen_s": 20},
        "backup": {"state": "ok", "age_h": 3.0},
    }
    doc.update(changes)
    return 200, json.dumps(doc).encode()


OK_LOGIN: Tuple[Optional[int], Optional[bytes]] = (200, b"<html>")


class Script:
    """Hands out scripted answers, one (login, health) pair per attempt."""

    def __init__(self, pairs: List[Tuple[Any, Any]]) -> None:
        self.pairs = pairs
        self.attempt = 0

    def __call__(self, url: str) -> Any:
        login, health = self.pairs[min(self.attempt, len(self.pairs) - 1)]
        if url.endswith("/v3/health"):
            self.attempt += 1
            return health
        assert url == BASE + "/login"
        return login


def _probe(pairs: List[Tuple[Any, Any]], attempts: int = 4) -> Dict[str, str]:
    return up.probe(BASE, fetcher=Script(pairs), attempts=attempts,
                    pause_s=0, sleep=lambda _s: None)


def test_a_healthy_site_raises_nothing() -> None:
    """All checks clean: no problems, state ok, no title."""
    problems = _probe([(OK_LOGIN, _health())])

    assert problems == {}
    assert up.state_of(problems) == "ok" and up.title_of(problems) == ""


def test_the_outage_is_reported_as_down() -> None:
    """The 2026-11-21 state: nothing answers behind the tunnel."""
    problems = _probe([((502, None), (502, None))])

    assert set(problems) == {"site", "api"}
    assert up.state_of(problems) == "down"
    assert up.title_of(problems) == up.TITLE_DOWN
    assert "HTTP 502" in problems["site"]


def test_no_answer_at_all_is_down() -> None:
    """A dead server or tunnel: connection errors, not HTTP codes."""
    problems = _probe([((None, None), (None, None))])

    assert up.state_of(problems) == "down"
    assert "没有响应" in problems["api"]


def test_a_blip_during_a_deploy_is_not_reported() -> None:
    """A problem must be there on every attempt: one clean attempt clears
    it (containers are recreated in about a minute during a deploy)."""
    problems = _probe([((502, None), (502, None)),
                       (OK_LOGIN, _health(gpu_worker={"alive": False})),
                       (OK_LOGIN, _health())])

    assert problems == {}


def test_a_dead_model_controller_is_degraded() -> None:
    """What the outage left behind: site up, controller silent for hours."""
    stuck = _health(model_service={"state": "stopped",
                                   "controller_seen_s": 81333})

    problems = _probe([(OK_LOGIN, stuck)])

    assert set(problems) == {"controller"}
    assert up.state_of(problems) == "degraded"
    assert up.title_of(problems) == up.TITLE_DEGRADED
    assert "1355 分钟" in problems["controller"]


def test_gpu_worker_and_stale_backup_are_degraded() -> None:
    """A missing heartbeat and a backup older than 36 hours both count."""
    problems = _probe([(OK_LOGIN, _health(gpu_worker={"alive": False},
                                          backup={"state": "stale"}))])

    assert set(problems) == {"gpu_worker", "backup"}
    assert up.state_of(problems) == "degraded"


def test_a_health_page_that_is_not_ours_counts_as_down() -> None:
    """A 200 that is not the health document (error page, challenge)."""
    problems = _probe([(OK_LOGIN, (200, b"<html>challenge</html>"))])

    assert set(problems) == {"api"}


def test_only_lasting_problems_survive_the_attempts() -> None:
    """Of two problems, the one that clears on a later attempt is dropped."""
    both = _health(gpu_worker={"alive": False}, backup={"state": "stale"})
    one = _health(backup={"state": "stale"})

    problems = _probe([(OK_LOGIN, both), (OK_LOGIN, one)])

    assert set(problems) == {"backup"}


def test_the_alert_text_names_time_problems_and_next_step() -> None:
    """The issue body carries Hong Kong time, each problem and where to
    look, and never the raw health document."""
    problems = {"site": "网站打不开：登录页HTTP 502",
                "api": "后端健康检查失败：HTTP 502"}
    now = dt.datetime(2026, 11, 20, 19, 0, tzinfo=dt.timezone.utc)

    body = up.alert_body(problems, BASE, 4, now=now)

    assert "2026-11-21 03:00（香港时间）" in body
    assert "- 网站打不开：登录页HTTP 502" in body
    assert "selfheal.py status" in body
    assert "controller_seen_s" not in body and "{" not in body


def test_main_writes_outputs_and_body(tmp_path: pathlib.Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The workflow reads state / title from GITHUB_OUTPUT and the body
    from a file; --fail-on-problem makes a pull-request run fail."""
    out, body = tmp_path / "out.txt", tmp_path / "body.md"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setattr(up, "fetch", lambda url: (503, None))
    monkeypatch.setattr(up.time, "sleep", lambda _s: None)
    monkeypatch.setattr(up, "probe",
                        lambda base, attempts, pause_s: up.evaluate(
                            up.fetch(base), up.fetch(base)))

    rc = up.main(["--base-url", BASE + "/", "--body-file", str(body),
                  "--fail-on-problem"])

    assert rc == 1
    written = out.read_text(encoding="utf-8")
    assert "state=down" in written and f"title={up.TITLE_DOWN}" in written
    assert "HTTP 503" in body.read_text(encoding="utf-8")


def test_a_drill_reports_down_without_probing(tmp_path: pathlib.Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """--simulate down lets the maintainer test the alert channel: no
    request is made and the text says it is a drill."""
    out, body = tmp_path / "out.txt", tmp_path / "body.md"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))

    def no_network(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("a drill must not probe")

    monkeypatch.setattr(up, "probe", no_network)

    rc = up.main(["--base-url", BASE, "--body-file", str(body),
                  "--simulate", "down"])

    assert rc == 0
    assert "state=down" in out.read_text(encoding="utf-8")
    assert "演练" in body.read_text(encoding="utf-8")
