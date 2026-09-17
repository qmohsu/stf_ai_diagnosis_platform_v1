"""PROD-08 (integration): rows → ``DiagDeps`` → one offline diagnosis.

Runs against the throwaway test database: a real vehicle and an uploaded
log become the dependency bundle, the TestModel drives a full round, and
a log of another vehicle is refused (FM-4).

Author: Xiangzhu Yan
"""

from __future__ import annotations

import pathlib
import uuid

import pytest
from pydantic_ai.models.test import TestModel

from tests.conftest import register_and_login, requires_db

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


@requires_db
async def test_bootstrap_loads_rows_and_runs_a_diagnosis(client, workshop_with_codes, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Vehicle + uploaded maxlog log → deps (identity from the vehicle row,
    no VIN from the log needed) → TestModel diagnosis completes with events
    and a report; another vehicle's log is refused."""
    from stf_v3.db import SessionLocal
    from stf_v3.diagnosis.agent.bootstrap import DepsNotFound, load_diag_deps
    from stf_v3.diagnosis.agent.main_agent import run_diagnosis
    from stf_v3.ingest.loader import LogOwnershipError
    from stf_v3.settings import settings

    workshop_id, codes = workshop_with_codes
    headers = await register_and_login(client, "tech1", codes["technician"])
    r = await client.post(f"/v3/workshops/{workshop_id}/vehicles", headers=headers, json={
        "vin": "JHMGK5830HX202404", "manufacturer": "Toyota", "model": "Hiace", "plate": "AB 1234",
    })
    assert r.status_code == 201, r.text
    vehicle_id = uuid.UUID(r.json()["id"])
    r = await client.post(f"/v3/workshops/{workshop_id}/vehicles", headers=headers, json={
        "vin": "1HGCM82633A123456", "manufacturer": "Toyota", "model": "Corolla",
    })
    assert r.status_code == 201, r.text
    other_id = uuid.UUID(r.json()["id"])
    csv = (FIXTURES / "obd_maxlog_no_vin.csv").read_bytes()
    r = await client.post(f"/v3/vehicles/{vehicle_id}/logs", headers=headers,
                          files={"file": ("trip.csv", csv, "text/csv")})
    assert r.status_code == 201, r.text
    log_id = uuid.UUID(r.json()["id"])

    async with SessionLocal() as session:
        deps = await load_diag_deps(session, vehicle_id, log_id, settings, locale="en")
        with pytest.raises(LogOwnershipError):
            await load_diag_deps(session, other_id, log_id, settings)
        with pytest.raises(DepsNotFound):
            await load_diag_deps(session, uuid.uuid4(), log_id, settings)
    assert deps.vehicle.vin == "JHMGK5830HX202404" and deps.log.format == "maxlog"
    assert deps.locale == "en" and deps.budgets.request_limit == settings.agent_request_limit
    out = await run_diagnosis(deps, TestModel(custom_output_text="**Fault identification** — test."))
    assert out.stopped_reason == "complete" and out.report.content_md.startswith("**Fault")
    assert out.events[0].event_type == "session_start" and out.events[-1].event_type == "done"
    assert out.events[0].payload["vehicle"].startswith("Toyota Hiace")
    assert sum(1 for e in out.events if e.event_type == "tool_call") == 12
