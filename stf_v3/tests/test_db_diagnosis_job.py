"""PROD-11 job tests on the throwaway database, driven by scripted models.

T-3 claim / stalled recovery, T-4 / T-5 event writer, T-6 one final
transaction + failure paths, T-7 status rules and partial reports, T-8
messages round trip, T-10 cancel while running / waiting, T-17 waiting for
the model, T-18 one diagnosis at a time, T-19 sweeping, T-20 vehicle
deleted while queued.

Author: Xiangzhu Yan
"""

from __future__ import annotations

import asyncio
import math
import time
import uuid
from typing import Any, List

import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from sqlalchemy import select, text

from tests.agent_helpers import TEXT, response, tool_call, which_agent
from tests.conftest import requires_db
from tests.diagnosis_helpers import (
    FakeQueue,
    conversation_row,
    events_of,
    install_fake_queue,
    no_sleep,
    ready,
    seed,
)

pytestmark = requires_db


def _settings(**overrides: Any) -> Any:
    from stf_v3.settings import settings

    base = dict(diagnosis_cancel_poll_s=0.05, diagnosis_event_flush_s=0.05,
                diagnosis_wait_poll_s=0.05, diagnosis_wait_event_s=0, default_locale="en")
    base.update(overrides)
    return settings.model_copy(update=base)


async def _queued(client: Any, workshop_with_codes: Any, monkeypatch: Any, **seed_kw: Any) -> Any:
    install_fake_queue(monkeypatch)
    wid, codes = workshop_with_codes
    s = await seed(client, wid, codes, **seed_kw)
    r = await client.post(f"/v3/vehicles/{s.vehicle_id}/diagnose", headers=s.headers,
                          json={"obd_log_id": str(s.log_id)})
    assert r.status_code == 202, r.text
    return s, uuid.UUID(r.json()["conversation_id"])


async def _run(cid: uuid.UUID, model: Any, **kw: Any) -> str:
    from stf_v3.diagnosis.job import run_conversation

    settings = kw.pop("settings", None) or _settings()
    return await run_conversation(cid, 1000, settings=settings, model_factory=lambda s: model,
                                  ready_fn=kw.pop("ready_fn", ready), sleep=kw.pop("sleep", asyncio.sleep), **kw)


async def _report(cid: uuid.UUID) -> Any:
    from stf_v3.db import SessionLocal
    from stf_v3.diagnosis.models import Report

    async with SessionLocal() as session:
        return (await session.execute(select(Report).where(Report.conversation_id == cid))).scalar_one_or_none()


def _types(events: List[Any]) -> List[str]:
    return [t for _, t, _ in events]


# ── T-7 / T-6 ① / T-8: a complete run ──────────────────────────────────


async def test_complete_run_writes_everything_in_one_final_transaction(client, workshop_with_codes, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """T-7 complete, T-6 ①, T-8: status done, full report, gapless events
    ending ``diagnosis_done`` (with report id) → ``done``; report, final
    events and finished_at share ONE transaction (same now()); messages
    are stored request / response and read back as Pydantic AI messages."""
    from stf_v3.db import SessionLocal
    from stf_v3.diagnosis.agent.memory import messages_from_jsonable
    from stf_v3.diagnosis.models import AuditEvent, Message

    _, cid = await _queued(client, workshop_with_codes, monkeypatch)
    status = await _run(cid, TestModel(custom_output_text="**Fault** — coolant sensor."))
    assert status == "done"
    conv = await conversation_row(cid)
    rep = await _report(cid)
    assert conv.status == "done" and conv.finished_at is not None and conv.error_code is None
    assert rep.partial is False and rep.stopped_reason == "complete" and rep.content_md.startswith("**Fault")
    evs = await events_of(cid)
    assert [s for s, _, _ in evs] == list(range(1, len(evs) + 1))
    types = _types(evs)
    assert types[0] == "waiting" and types[1] == "session_start" and types[-2:] == ["diagnosis_done", "done"]
    assert evs[-2][2]["report_id"] == str(rep.id)
    async with SessionLocal() as session:
        done_at = (await session.execute(select(AuditEvent.created_at).where(
            AuditEvent.conversation_id == cid, AuditEvent.event_type == "done"))).scalar_one()
        msgs = (await session.execute(select(Message).where(Message.conversation_id == cid)
                                      .order_by(Message.seq))).scalars().all()
    assert done_at == rep.created_at == conv.finished_at          # one transaction (FM-3)
    assert {m.kind for m in msgs} == {"request", "response"} and msgs[0].kind == "request"
    restored = messages_from_jsonable([m.content for m in msgs])
    assert len(restored) == len(msgs) and any(m.token_usage for m in msgs if m.kind == "response")


# ── T-7 status rules ───────────────────────────────────────────────────


async def test_budget_stop_with_text_is_done_and_partial(client, workshop_with_codes, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """T-7: a usage gate after some text → done + partial report with the
    stop reason; the engine's error event carries a code and a sentence."""
    _, cid = await _queued(client, workshop_with_codes, monkeypatch)

    def fn(messages: List[ModelMessage], info: AgentInfo) -> ModelResponse:
        return response(TEXT("Working hypothesis: coolant temperature sensor."), tool_call("list_dtcs"))

    status = await _run(cid, FunctionModel(fn), settings=_settings(agent_request_limit=1))
    rep = await _report(cid)
    assert status == "done" and rep.partial is True and rep.stopped_reason == "budget"
    assert "Working hypothesis" in rep.content_md and rep.limitations
    err = [p for _, t, p in await events_of(cid) if t == "error"][0]
    assert err["code"] == "stopped_budget" and "partial" in err["message"] and "Exceeded" not in err["message"]
    types = _types(await events_of(cid))
    assert types[-3:] == ["error", "diagnosis_done", "done"]      # FM-46 order kept


async def test_zero_output_is_an_error_without_report(client, workshop_with_codes, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """T-7: stopped before any text → error ``run_failed``, no report,
    no ``diagnosis_done``; the report endpoint says ``report_unavailable``."""
    s, cid = await _queued(client, workshop_with_codes, monkeypatch)

    def fn(messages: List[ModelMessage], info: AgentInfo) -> ModelResponse:
        return response(tool_call("list_dtcs"))

    assert await _run(cid, FunctionModel(fn), settings=_settings(agent_request_limit=1)) == "error"
    conv = await conversation_row(cid)
    assert conv.error_code == "run_failed" and await _report(cid) is None
    types = _types(await events_of(cid))
    assert "diagnosis_done" not in types and types[-1] == "done"
    r = await client.get(f"/v3/conversations/{cid}/report", headers=s.headers)
    assert r.status_code == 404 and r.json()["code"] == "report_unavailable"


async def test_partial_report_never_calls_the_model_again(client, workshop_with_codes, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """T-7 / FM-10: the model fails from its 2nd request on; the partial
    report is built from what exists (no 3rd call)."""
    _, cid = await _queued(client, workshop_with_codes, monkeypatch)
    calls = {"n": 0}

    def fn(messages: List[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls["n"] += 1
        if calls["n"] >= 2:
            raise ModelHTTPError(status_code=500, model_name="scripted", body="boom")
        return response(TEXT("Early finding: DTC P0117 present."), tool_call("list_dtcs"))

    assert await _run(cid, FunctionModel(fn)) == "done"
    rep = await _report(cid)
    assert rep.partial and rep.stopped_reason == "error" and "Early finding" in rep.content_md
    assert calls["n"] == 2


# ── T-10 cancel ────────────────────────────────────────────────────────


async def test_cancel_while_running_stops_before_the_next_request(client, workshop_with_codes, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """T-10 / FM-49: the flag set during a model call is picked up by the
    off-loop poller; the next request is torn down as it starts (Pydantic
    AI cancels the task driving the run), so no second answer is ever
    produced; status cancelled with the partial text kept; the event loop
    never stalls (lag < 0.5 s)."""
    from stf_v3.db import SessionLocal
    from stf_v3.diagnosis.models import DiagnosisConversation

    _, cid = await _queued(client, workshop_with_codes, monkeypatch)
    calls = {"n": 0, "answered": 0}

    async def fn(messages: List[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls["n"] += 1
        await asyncio.sleep(0.05)              # a real request takes time; cancel lands here
        if calls["n"] == 1:
            async with SessionLocal() as session:
                await session.execute(text("UPDATE diagnosis_conversations SET cancel_requested = true WHERE id = :i"),
                                      {"i": cid})
                await session.commit()
            await asyncio.sleep(0.3)
        calls["answered"] += 1
        return response(TEXT("Partial: checked DTCs."), tool_call("list_dtcs"))

    lag = {"max": 0.0}

    async def ticker() -> None:
        last = time.monotonic()
        while True:
            await asyncio.sleep(0.01)
            now = time.monotonic()
            lag["max"] = max(lag["max"], now - last - 0.01)
            last = now

    t = asyncio.create_task(ticker())
    status = await _run(cid, FunctionModel(fn))
    t.cancel()
    assert status == "cancelled" and calls["answered"] == 1 and calls["n"] <= 2
    assert (await conversation_row(cid)).status == "cancelled"
    assert (await _report(cid)).partial is True
    assert _types(await events_of(cid))[-1] == "done" and lag["max"] < 0.5
    assert DiagnosisConversation is not None


async def test_cancel_while_waiting_for_the_model(client, workshop_with_codes, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """T-10: cancel during the model wait ends the run within a poll."""
    _, cid = await _queued(client, workshop_with_codes, monkeypatch)
    from stf_v3.db import SessionLocal

    async def never(_s: Any) -> bool:
        async with SessionLocal() as session:
            await session.execute(text("UPDATE diagnosis_conversations SET cancel_requested = true WHERE id = :i"),
                                  {"i": cid})
            await session.commit()
        return False

    t0 = time.monotonic()
    assert await _run(cid, TestModel(), ready_fn=never) == "cancelled"
    assert time.monotonic() - t0 < 5 and (await conversation_row(cid)).status == "cancelled"


# ── T-17 waiting for the model ─────────────────────────────────────────


async def _state(**cols: Any) -> None:
    from stf_v3.db import SessionLocal

    sets = ", ".join(f"{k} = {v}" for k, v in cols.items())
    async with SessionLocal() as session:
        await session.execute(text(
            f"INSERT INTO model_service_state (id) VALUES (1) ON CONFLICT (id) DO NOTHING; "
            f"UPDATE model_service_state SET {sets} WHERE id = 1"))
        await session.commit()


async def _never(_s: Any) -> bool:
    return False


async def test_wait_reasons_follow_the_controller_and_time_out(client, workshop_with_codes, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """T-17 / FM-31 / FM-22: no controller → ``controller_unresponsive``;
    the cap (here 1 s) ends the run as ``model_unavailable``; the host is
    asked to reconcile; events are ``waiting`` with reason + sentence."""
    _, cid = await _queued(client, workshop_with_codes, monkeypatch)
    asked = {"n": 0}

    async def ask() -> None:
        asked["n"] += 1

    status = await _run(cid, TestModel(), ready_fn=_never, request_reconcile=ask,
                        settings=_settings(diagnosis_model_wait_s=1))
    assert status == "error" and (await conversation_row(cid)).error_code == "model_unavailable"
    waits = [p for _, t, p in await events_of(cid) if t == "waiting"]
    assert waits[0]["reason"] == "queued" and waits[1]["reason"] == "controller_unresponsive"
    assert all(w["message"] for w in waits) and asked["n"] >= 1


@pytest.mark.parametrize("cols, reason", [
    ({"state": "'starting'", "requested_at": "now()", "controller_seen_at": "now()"}, "model_starting"),
    ({"state": "'blocked'", "blocked_reason": "'other_tenant'", "controller_seen_at": "now()"}, "gpu_busy"),
    ({"state": "'blocked'", "blocked_reason": "'manual_converting'", "controller_seen_at": "now()"},
     "manual_converting"),
])
async def test_wait_reason_comes_from_the_state_row(client, workshop_with_codes, monkeypatch, cols, reason) -> None:  # type: ignore[no-untyped-def]
    """T-17: starting (with an ETA), another team's GPU use, our own manual
    conversion — each shows its own reason."""
    _, cid = await _queued(client, workshop_with_codes, monkeypatch)
    await _state(**cols)
    await _run(cid, TestModel(), ready_fn=_never, settings=_settings(diagnosis_model_wait_s=1))
    waits = [p for _, t, p in await events_of(cid) if t == "waiting"]
    assert waits[1]["reason"] == reason
    if reason == "model_starting":
        assert waits[1]["eta_min"] >= 1


async def test_failed_start_ends_waiting_runs_at_once(client, workshop_with_codes, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """T-17 / FM-24: a start that failed after the click ends the run now."""
    _, cid = await _queued(client, workshop_with_codes, monkeypatch)
    await _state(state="'failed'", failed_at="now()", cooldown_until="now() + interval '15 minutes'",
                 controller_seen_at="now()")
    t0 = time.monotonic()
    assert await _run(cid, TestModel(), ready_fn=_never) == "error"
    assert (await conversation_row(cid)).error_code == "model_start_failed" and time.monotonic() - t0 < 5


async def test_the_wait_counts_from_the_click(client, workshop_with_codes, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """T-17 / FM-22: a run queued long ago behind others fails fast instead of
    starting its own full wait."""
    _, cid = await _queued(client, workshop_with_codes, monkeypatch)
    from stf_v3.db import SessionLocal

    async with SessionLocal() as session:
        await session.execute(text("UPDATE diagnosis_conversations SET created_at = now() - interval '2 hours' "
                                   "WHERE id = :i"), {"i": cid})
        await session.commit()
    t0 = time.monotonic()
    assert await _run(cid, TestModel(), ready_fn=_never) == "error"
    assert time.monotonic() - t0 < 3


async def test_the_run_budget_starts_after_the_model_is_ready(client, workshop_with_codes, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """T-17 / FM-41: 2 s of waiting + a 2 s model call under a 3 s wall
    clock still completes — the clock started when the model was ready."""
    _, cid = await _queued(client, workshop_with_codes, monkeypatch)
    t0 = time.monotonic()

    async def later(_s: Any) -> bool:
        return time.monotonic() - t0 >= 2.0

    async def fn(messages: List[ModelMessage], info: AgentInfo) -> ModelResponse:
        await asyncio.sleep(2.0)
        return response(TEXT("Done after a slow call."))

    status = await _run(cid, FunctionModel(fn), ready_fn=later, settings=_settings(agent_wall_clock_s=3.0))
    assert status == "done" and (await _report(cid)).partial is False


# ── T-4 / T-5 event writer ─────────────────────────────────────────────


async def test_events_are_visible_while_running_and_stay_in_order(client, workshop_with_codes, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """T-4 / FM-1 / FM-19: another connection sees events within the flush
    interval; 200 rapid events end gapless, commit order = seq order."""
    from stf_v3.db import SessionLocal
    from stf_v3.diagnosis import store
    from stf_v3.diagnosis.models import AuditEvent

    _, cid = await _queued(client, workshop_with_codes, monkeypatch)
    w = store.EventWriter(SessionLocal, cid, 2, flush_s=0.2).start()
    for i in range(3):
        w.put("token", {"text": str(i)})
    await asyncio.sleep(0.6)
    assert len(await events_of(cid)) == 4                         # visible before close
    for i in range(200):
        w.put("token", {"text": f"x{i}"})
    assert await w.close() == 205
    async with SessionLocal() as session:
        rows = (await session.execute(select(AuditEvent.id, AuditEvent.seq).where(
            AuditEvent.conversation_id == cid).order_by(AuditEvent.id))).all()
    assert [s for _, s in rows] == list(range(1, 205))


async def test_closing_paths_continue_after_the_stored_maximum(client, workshop_with_codes, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """T-5 / FM-19: a sweeper / cancel close continues at max(seq)+1."""
    from stf_v3.db import SessionLocal
    from stf_v3.diagnosis import store

    _, cid = await _queued(client, workshop_with_codes, monkeypatch)
    w = store.EventWriter(SessionLocal, cid, 2).start()
    for i in range(5):
        w.put("token", {"text": str(i)})
    await w.close()
    assert await store.close_with_error(SessionLocal, cid, "diagnosis_interrupted", locale="en")
    evs = await events_of(cid)
    assert [s for s, _, _ in evs] == list(range(1, 9)) and _types(evs)[-2:] == ["error", "done"]
    assert not await store.close_with_error(SessionLocal, cid, "internal_error", locale="en")


# ── T-6 failure paths ──────────────────────────────────────────────────


async def test_a_failed_final_transaction_leaves_nothing_half_written(client, workshop_with_codes, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """T-6 ②: the final transaction fails → no report, no diagnosis_done;
    the job closes the run as error + done in its own transaction."""
    from stf_v3.diagnosis import job

    _, cid = await _queued(client, workshop_with_codes, monkeypatch)

    async def broken(*a: Any, **k: Any) -> Any:
        raise RuntimeError("disk full (test) postgresql://secret@host/db")

    monkeypatch.setattr(job, "finalize", broken)
    assert await _run(cid, TestModel(custom_output_text="ok")) == "error"
    conv = await conversation_row(cid)
    assert conv.status == "error" and conv.error_code == "internal_error" and await _report(cid) is None
    evs = await events_of(cid)
    assert "diagnosis_done" not in _types(evs) and _types(evs)[-2:] == ["error", "done"]
    assert "postgresql://" not in str(evs) and "postgresql://" not in (conv.error_message or "")   # FM-38


async def test_nul_and_nan_are_made_storable(client, workshop_with_codes, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """T-6 ③ / FM-2: NUL characters and NaN in messages / report do not
    break the final transaction."""
    from stf_v3.db import SessionLocal
    from stf_v3.diagnosis import store

    assert store.json_safe({"a\x00": ["x\x00y", math.nan, math.inf, 1.5]}) == {"a": ["xy", None, None, 1.5]}
    _, cid = await _queued(client, workshop_with_codes, monkeypatch)
    async with SessionLocal() as session:
        async with session.begin():
            (await store.lock_conversation(session, cid)).status = "running"
    await store.finalize(SessionLocal, cid, status="done", report={
        "content_md": "ok", "citations": [{"ref": "a\x00"}], "model": "t", "partial": False,
        "stopped_reason": "complete", "limitations": ["n\x00"]},
        messages=[{"kind": "request", "parts": [{"content": "raw\x00log", "x": math.nan}]}],
        tail=[("done", {"v": math.nan})], model="t")
    rep = await _report(cid)
    assert rep.citations == [{"ref": "a"}] and rep.limitations == ["n"]


async def test_when_even_the_error_path_fails_the_sweeper_closes_it(client, workshop_with_codes, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """T-6 ④ / FM-12: finalize AND the error close fail → the run stays
    running; once its job is finished, the sweeper closes it."""
    from stf_v3.db import SessionLocal
    from stf_v3.diagnosis import job, store

    _, cid = await _queued(client, workshop_with_codes, monkeypatch)

    async def broken(*a: Any, **k: Any) -> Any:
        raise RuntimeError("down")

    monkeypatch.setattr(job, "finalize", broken)
    monkeypatch.setattr(job, "close_with_error", broken)
    assert await _run(cid, TestModel(custom_output_text="ok")) == "error"
    assert (await conversation_row(cid)).status == "running"

    async def finished(_job_id: int) -> str:
        return "succeeded"

    counts = await store.sweep(SessionLocal, finished, stale_s=300, locale="en")
    assert counts["job_finished"] == 1
    conv = await conversation_row(cid)
    assert conv.status == "error" and conv.error_code == "diagnosis_interrupted"


# ── T-3 claim and stalled recovery ─────────────────────────────────────


async def test_only_one_worker_can_claim_a_conversation(client, workshop_with_codes, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """T-3 ① / FM-17: two jobs for the same conversation — one runs, the
    other exits without touching it."""
    _, cid = await _queued(client, workshop_with_codes, monkeypatch)
    a, b = await asyncio.gather(_run(cid, TestModel(custom_output_text="a")),
                                _run(cid, TestModel(custom_output_text="b")))
    assert sorted([a, b]) == ["done", "skipped"]
    assert _types(await events_of(cid)).count("session_start") == 1


async def test_stalled_diagnosis_is_closed_not_requeued(client, workshop_with_codes, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """T-3 ③ / FM-17: the stalled-job recovery fails a stalled diagnosis job
    and closes its conversation; other stalled jobs are still retried."""
    from types import SimpleNamespace

    from stf_v3.db import SessionLocal
    from stf_v3.diagnosis import store
    from stf_v3.jobs import maintenance
    from stf_v3.jobs.app import app

    _, cid = await _queued(client, workshop_with_codes, monkeypatch)
    async with SessionLocal() as session:
        async with session.begin():
            conv = await store.lock_conversation(session, cid)
            conv.status, conv.job_id = "running", 4242
    seen = {"failed": [], "retried": []}

    class FakeManager:
        async def get_stalled_jobs(self, seconds_since_heartbeat: float) -> Any:
            return [SimpleNamespace(id=4242, task_name="diagnosis.run", attempts=0),
                    SimpleNamespace(id=7, task_name="knowledge.ingest_manual", attempts=0)]

        async def finish_job_by_id_async(self, job_id: int, status: Any, delete_job: bool) -> None:
            seen["failed"].append(job_id)

        async def retry_job(self, job: Any) -> None:
            seen["retried"].append(job.id)

        async def prune_stalled_workers(self, seconds_since_heartbeat: float) -> None:
            return None

    monkeypatch.setattr(app, "job_manager", FakeManager())
    await maintenance.recover_stalled.__wrapped__(0) if hasattr(maintenance.recover_stalled, "__wrapped__") \
        else await maintenance.recover_stalled.func(0)
    assert seen == {"failed": [4242], "retried": [7]}
    conv = await conversation_row(cid)
    assert conv.status == "error" and conv.error_code == "diagnosis_interrupted"
    assert _types(await events_of(cid))[-2:] == ["error", "done"]


# ── T-19 sweeping ──────────────────────────────────────────────────────


async def test_sweeper_judges_by_the_job_not_by_elapsed_time(client, workshop_with_codes, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """T-19 / FM-9 / FM-35: queued without a job closes only after the stale
    window (database time); a 70-minute run whose job is alive is left
    alone; a run whose job is gone is closed."""
    from stf_v3.db import SessionLocal
    from stf_v3.diagnosis import store

    _, cid = await _queued(client, workshop_with_codes, monkeypatch)
    async with SessionLocal() as session:
        await session.execute(text("UPDATE diagnosis_conversations SET job_id = NULL WHERE id = :i"), {"i": cid})
        await session.commit()
    statuses = {}

    async def status(job_id: int) -> Any:
        return statuses.get(job_id)

    assert (await store.sweep(SessionLocal, status, stale_s=300, locale="en"))["no_job"] == 0
    async with SessionLocal() as session:
        await session.execute(text("UPDATE diagnosis_conversations SET created_at = now() - interval '10 minutes' "
                                   "WHERE id = :i"), {"i": cid})
        await session.commit()
    assert (await store.sweep(SessionLocal, status, stale_s=300, locale="en"))["no_job"] == 1
    assert (await conversation_row(cid)).error_code == "queue_unavailable"
    # a long, healthy run
    async with SessionLocal() as session:
        await session.execute(text(
            "UPDATE diagnosis_conversations SET status = 'running', job_id = 55, error_code = NULL, "
            "created_at = now() - interval '70 minutes' WHERE id = :i"), {"i": cid})
        await session.commit()
    statuses[55] = "doing"
    assert await store.sweep(SessionLocal, status, stale_s=300, locale="en") == {"no_job": 0, "job_finished": 0}
    assert (await conversation_row(cid)).status == "running"
    statuses.pop(55)
    assert (await store.sweep(SessionLocal, status, stale_s=300, locale="en"))["job_finished"] == 1


# ── T-20 vehicle deleted while queued ──────────────────────────────────


async def test_vehicle_deleted_while_queued(client, workshop_with_codes, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """T-20 / FM-52: the run ends as ``vehicle_deleted`` and frees the
    vehicle's one-at-a-time slot."""
    s, cid = await _queued(client, workshop_with_codes, monkeypatch, with_manager=True)
    r = await client.delete(f"/v3/vehicles/{s.vehicle_id}", headers=s.manager_headers)
    assert r.status_code == 204
    assert await _run(cid, TestModel()) == "error"
    conv = await conversation_row(cid)
    assert conv.status == "error" and conv.error_code == "vehicle_deleted"


# ── T-18 one diagnosis at a time ───────────────────────────────────────


async def test_the_queue_hands_out_one_diagnosis_at_a_time(clean_db) -> None:  # type: ignore[no-untyped-def]
    """T-18 / FM-16: with three diagnosis jobs queued, the worker gets the
    next one only after the running one ends (lock ``diagnosis-model``);
    default-queue jobs are not blocked meanwhile."""
    from procrastinate.jobs import Status

    from stf_v3.db import engine
    from stf_v3.diagnosis.tasks import run_diagnosis_job
    from stf_v3.jobs.app import app
    from stf_v3.jobs.drill import sleep_task

    async with app.open_async():
        try:
            for _ in range(3):
                await run_diagnosis_job.defer_async(conversation_id=str(uuid.uuid4()))
            await sleep_task.defer_async(seconds=1)
            manager = app.job_manager
            worker = await manager.register_worker()
            first = await manager.fetch_job(queues=["diagnosis"], worker_id=worker)
            assert first is not None
            assert await manager.fetch_job(queues=["diagnosis"], worker_id=worker) is None
            assert await manager.fetch_job(queues=["default"], worker_id=worker) is not None
            await manager.finish_job_by_id_async(first.id, Status.SUCCEEDED, delete_job=False)
            assert await manager.fetch_job(queues=["diagnosis"], worker_id=worker) is not None
        finally:
            async with engine.begin() as conn:
                await conn.execute(text("TRUNCATE procrastinate_jobs CASCADE"))
