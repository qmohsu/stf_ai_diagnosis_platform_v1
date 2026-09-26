"""PROD-11 contract and configuration checks (offline).

T-12: one event registry feeds the contract (11 names); the committed
contract carries only fake VINs; SSE usage is documented.  T-13: public
error payloads never carry raw exception text.  T-18: the worker
commands consume the right queues (one diagnosis at a time by lock).

Author: Xiangzhu Yan
"""

from __future__ import annotations

import json
import pathlib
import re

from stf_v3.diagnosis.agent.events import EVENT_TYPES
from stf_v3.diagnosis.job import _public_error_payload
from stf_v3.diagnosis.texts import ERROR_CODES, STOP_REASONS, WAIT_REASONS, error_text, wait_text

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "docs" / "api" / "v3_openapi.json"
FAKE_VINS = {"JHMGK5830HX202404", "1HGCM82633A123456"}
_VIN = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")


def _openapi() -> dict:
    from stf_v3.main import app

    return app.openapi()


def test_the_contract_lists_exactly_the_registry_event_names() -> None:
    """T-12 / FM-13: the EventType enum in OpenAPI = the engine registry
    (eleven names, ``waiting`` included)."""
    schemas = _openapi()["components"]["schemas"]
    assert schemas["EventType"]["enum"] == list(EVENT_TYPES)
    assert "waiting" in EVENT_TYPES and len(EVENT_TYPES) == 11


def test_the_contract_documents_the_stream_and_its_errors() -> None:
    """T-12 / FM-14 / FM-37: the events endpoint documents SSE (fetch, curl,
    Last-Event-ID, header-only token) and the JSON replay default."""
    op = _openapi()["paths"]["/v3/conversations/{conversation_id}/events"]["get"]
    text = op["description"]
    for needle in ("text/event-stream", "Last-Event-ID", "curl -N", "fetch()", "Authorization",
                   "keep reading until `done`"):
        assert needle in text, needle
    assert "text/event-stream" in op["responses"]["200"]["content"]
    assert not any(p["name"] in ("token", "access_token") for p in op.get("parameters", []))


def test_committed_contract_has_no_real_vin() -> None:
    """T-12 / FM-39: only the fixture VINs may appear in the contract."""
    if not CONTRACT.is_file():
        return
    found = {v for v in _VIN.findall(CONTRACT.read_text(encoding="utf-8"))
             if re.search(r"[A-Z]", v) and re.search(r"\d", v)}
    assert found <= FAKE_VINS, found


def test_public_error_payload_hides_the_exception() -> None:
    """T-13 / FM-38: the engine's error event keeps the stop reason, gets a
    code + sentence, and loses the raw exception text."""
    raw = {"stopped_reason": "error", "partial_report_chars": 10,
           "message": "ModelHTTPError: status_code: 500, url http://127.0.0.1:8010/v1 /app/data/x.csv"}
    out = _public_error_payload(raw, "zh-TW")
    assert out["code"] == "stopped_error" and out["message"] == STOP_REASONS["error"]["zh-TW"]
    assert "127.0.0.1" not in json.dumps(out, ensure_ascii=False) and "/app/" not in out["message"]


def test_every_code_has_a_sentence_in_every_locale() -> None:
    """T-13: user-facing sentences exist for each code in zh-TW / zh-CN / en."""
    for table in (ERROR_CODES, STOP_REASONS, WAIT_REASONS):
        for code, texts in table.items():
            assert set(texts) == {"zh-TW", "zh-CN", "en"}, code
    assert error_text("unknown_code", "en") == ERROR_CODES["internal_error"]["en"]
    assert wait_text("queued", "zh-CN", ahead=2) == "排队中，前面还有 2 个诊断"


def test_worker_commands_consume_the_right_queues() -> None:
    """T-18 / FM-16: the container worker takes ``default`` + ``diagnosis``
    (diagnosis jobs share one lock); the host worker takes ``gpu`` + ``llm``
    (manual ingests share one lock)."""
    from stf_v3.diagnosis.tasks import DIAGNOSIS_LOCK, run_diagnosis_job
    from stf_v3.knowledge.tasks import INGEST_LOCK, ingest_manual

    compose_path = ROOT / "infra" / "docker-compose.v3.yml"
    if compose_path.is_file():                      # absent in the portable copy (D3 ①)
        compose = compose_path.read_text(encoding="utf-8")
        assert '"-q", "default,diagnosis", "--concurrency", "2"' in compose
    unit_path = pathlib.Path(__file__).resolve().parents[1] / "gpu_worker" / "stf-v3-gpu-worker.service"
    unit = unit_path.read_text(encoding="utf-8")
    assert "worker -q gpu,llm --concurrency 2" in unit
    assert run_diagnosis_job.lock == DIAGNOSIS_LOCK and ingest_manual.lock == INGEST_LOCK
    from stf_v3.diagnosis.tasks import RECONCILE_LOCK, RECONCILE_QUEUEING_LOCK, llm_reconcile

    assert llm_reconcile.lock == RECONCILE_LOCK and llm_reconcile.queueing_lock == RECONCILE_QUEUEING_LOCK
