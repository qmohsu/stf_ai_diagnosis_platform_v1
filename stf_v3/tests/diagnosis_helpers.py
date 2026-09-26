"""Shared helpers for the PROD-11 diagnosis tests (database-backed).

Author: Xiangzhu Yan
"""

from __future__ import annotations

import itertools
import pathlib
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from tests.conftest import register_and_login

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
FAKE_VIN = "JHMGK5830HX202404"
FAKE_VIN_B = "1HGCM82633A123456"


@dataclass
class Seeded:
    """One workshop member with one vehicle and one uploaded log."""

    workshop_id: str
    headers: Dict[str, str]
    vehicle_id: uuid.UUID
    log_id: uuid.UUID
    manager_headers: Optional[Dict[str, str]] = None


async def seed(client: Any, workshop_id: str, codes: Dict[str, str], *, user: str = "tech1",
               vin: str = FAKE_VIN, with_manager: bool = False) -> Seeded:
    """Technician (+ optionally the manager) → vehicle → uploaded maxlog log."""
    headers = await register_and_login(client, user, codes["technician"])
    manager = await register_and_login(client, user + "_mgr", codes["manager"]) if with_manager else None
    r = await client.post(f"/v3/workshops/{workshop_id}/vehicles", headers=headers, json={
        "vin": vin, "manufacturer": "Toyota", "model": "Hiace"})
    assert r.status_code == 201, r.text
    vehicle_id = uuid.UUID(r.json()["id"])
    csv = (FIXTURES / "obd_maxlog_no_vin.csv").read_bytes()
    r = await client.post(f"/v3/vehicles/{vehicle_id}/logs", headers=headers,
                          files={"file": ("trip.csv", csv, "text/csv")})
    assert r.status_code == 201, r.text
    return Seeded(workshop_id, headers, vehicle_id, uuid.UUID(r.json()["id"]), manager)


async def second_workshop(name: str = "Other Workshop") -> Tuple[str, Dict[str, str]]:
    """Another workshop with one manager + one technician code."""
    from stf_v3.db import SessionLocal
    from stf_v3.workshops import service

    async with SessionLocal() as session:
        workshop = await service.create_workshop(session, name)
        m = await service.issue_invite_codes(session, workshop.id, "manager", 1, None)
        t = await service.issue_invite_codes(session, workshop.id, "technician", 1, None)
        return str(workshop.id), {"manager": m[0].code, "technician": t[0].code}


@dataclass
class FakeQueue:
    """Stands in for the diagnosis tasks' queue calls (no worker in tests)."""

    fail_defer: bool = False
    statuses: Dict[int, Optional[str]] = field(default_factory=dict)
    deferred: List[uuid.UUID] = field(default_factory=list)
    cancelled: List[int] = field(default_factory=list)
    _ids: Any = field(default_factory=lambda: itertools.count(1000))

    async def defer(self, conversation_id: uuid.UUID) -> int:
        if self.fail_defer:
            raise RuntimeError("queue down (test)")
        self.deferred.append(conversation_id)
        job_id = next(self._ids)
        self.statuses[job_id] = "todo"
        return job_id

    async def status(self, job_id: int) -> Optional[str]:
        return self.statuses.get(job_id)

    async def cancel(self, job_id: int) -> None:
        self.cancelled.append(job_id)
        self.statuses[job_id] = "cancelled"


def install_fake_queue(monkeypatch: Any, queue: Optional[FakeQueue] = None) -> FakeQueue:
    """Routes ``diagnosis.tasks`` defer / status / cancel to a ``FakeQueue``."""
    from stf_v3.diagnosis import tasks

    queue = queue or FakeQueue()
    monkeypatch.setattr(tasks, "defer_diagnosis", queue.defer)
    monkeypatch.setattr(tasks, "job_status", queue.status)
    monkeypatch.setattr(tasks, "cancel_job", queue.cancel)
    return queue


async def events_of(conversation_id: uuid.UUID) -> List[Tuple[int, str, Dict[str, Any]]]:
    """``(seq, event_type, payload)`` of a conversation, in seq order."""
    from stf_v3.db import SessionLocal
    from stf_v3.diagnosis.store import read_events

    async with SessionLocal() as session:
        return [(r.seq, r.event_type, r.payload) for r in await read_events(session, conversation_id, 0, 10_000)]


async def conversation_row(conversation_id: uuid.UUID) -> Any:
    """The conversation row (fresh session)."""
    from stf_v3.db import SessionLocal
    from stf_v3.diagnosis.models import DiagnosisConversation

    async with SessionLocal() as session:
        return await session.get(DiagnosisConversation, conversation_id)


async def ready(_settings: Any) -> bool:
    """Model readiness stub: always ready."""
    return True


async def no_sleep(_s: float) -> None:
    """Sleep stub for wait loops."""
    return None
