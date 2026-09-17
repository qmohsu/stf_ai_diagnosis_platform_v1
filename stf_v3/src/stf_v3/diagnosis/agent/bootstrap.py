"""Build ``DiagDeps`` from database rows (the only DB access of the runtime).

Used by the server run script today and by PROD-11's diagnosis job.  The
vehicle record is the identity source; the log row must belong to the
vehicle (FM-4); the manual inventory is every ``ingested`` manual.

Author: Xiangzhu Yan
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Callable, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from stf_v3.diagnosis.agent.deps import Budgets, DiagDeps, LogInfo, ManualInfo, VehicleInfo
from stf_v3.diagnosis.agent.events import EventCallback, EventSink
from stf_v3.ingest.loader import LogOwnershipError
from stf_v3.ingest.models import ObdLog
from stf_v3.knowledge.models import Manual
from stf_v3.vehicles.models import Vehicle


class DepsNotFound(LookupError):
    """Vehicle or log row missing (or soft-deleted)."""


async def load_manual_inventory(session: AsyncSession) -> List[ManualInfo]:
    """Every ingested manual as ``ManualInfo`` (ordered by creation)."""
    rows = (await session.execute(
        select(Manual).where(Manual.status == "ingested").order_by(Manual.created_at)
    )).scalars().all()
    return [
        ManualInfo(
            id=str(r.id), manufacturer=r.manufacturer, vehicle_model=r.vehicle_model,
            factory_code=r.factory_code, md_file_path=r.md_file_path, page_count=r.page_count,
            section_count=r.section_count, language=r.language,
        )
        for r in rows
    ]


async def load_diag_deps(
    session: AsyncSession,
    vehicle_id: uuid.UUID,
    log_id: uuid.UUID,
    settings: Any,
    *,
    locale: Optional[str] = None,
    event_callback: Optional[EventCallback] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    model_is_local: Optional[bool] = None,
) -> DiagDeps:
    """Load vehicle + log + manuals and assemble the dependency bundle.

    Raises:
        DepsNotFound: Vehicle or log row missing.
        LogOwnershipError: The log belongs to another vehicle.
    """
    vehicle = await session.get(Vehicle, vehicle_id)
    if vehicle is None or vehicle.deleted_at is not None:
        raise DepsNotFound(f"vehicle {vehicle_id} not found")
    log = await session.get(ObdLog, log_id)
    if log is None:
        raise DepsNotFound(f"log {log_id} not found")
    if log.vehicle_id != vehicle.id:
        raise LogOwnershipError(f"log {log_id} belongs to vehicle {log.vehicle_id}, not {vehicle.id}")
    manuals = await load_manual_inventory(session)
    return DiagDeps(
        vehicle=VehicleInfo(
            id=vehicle.id, manufacturer=vehicle.manufacturer, model=vehicle.model, vin=vehicle.vin,
            plate=vehicle.plate, nickname=vehicle.nickname,
        ),
        log=LogInfo(
            id=log.id, vehicle_id=log.vehicle_id, raw_path=log.raw_path, format=log.format,
            recorded_start=log.recorded_start, recorded_end=log.recorded_end,
            vin_from_log=log.vin_from_log, original_filename=log.original_filename,
        ),
        manuals=manuals,
        log_root=Path(settings.obd_log_storage_path),
        manual_root=Path(settings.manual_storage_path),
        budgets=Budgets.from_settings(settings),
        locale=locale or settings.default_locale,
        model_is_local=settings.llm_is_local if model_is_local is None else model_is_local,
        images_enabled=settings.manual_images_enabled,
        events=EventSink(event_callback),
        cancel_check=cancel_check,
    )
