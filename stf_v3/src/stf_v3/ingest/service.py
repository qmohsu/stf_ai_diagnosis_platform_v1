"""Ingest service: store an uploaded log, never diagnose (design doc §1.2).

``store_log()`` is the single write path shared by member upload
(``POST /v3/vehicles/{id}/logs``) and device upload
(``POST /v3/ingest/device``).  Order of checks:

1. size ≤ ``settings.max_upload_bytes``      → else 413 ``file_too_large``
2. format ∈ {tsv, yamaha}                    → else 422 ``unsupported_format``
3. sha256 already stored for this vehicle   → return existing, duplicate=True
4. VIN in file == vehicle VIN (if present)   → else 422 ``vin_mismatch``
   (decision D2, 2026-09-13: reject, never store a log under the wrong car)
5. write file, insert row (row failure deletes the file)

Author: Xiangzhu Yan
"""

import hashlib
import uuid
from typing import List, Optional, Tuple

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from stf_v3.errors import ApiError
from stf_v3.ingest.models import ObdLog
from stf_v3.ingest.parsers import sniff
from stf_v3.ingest.storage import LogStorage
from stf_v3.settings import settings
from stf_v3.vehicles.models import Vehicle

log = structlog.get_logger("stf_v3.ingest")


def storage() -> LogStorage:
    """Storage bound to the configured root (read per call so tests can
    point it at a temp dir)."""
    return LogStorage(settings.obd_log_storage_path)


def sha256_hex(data: bytes) -> str:
    """Returns the hex sha256 of ``data``."""
    return hashlib.sha256(data).hexdigest()


async def find_by_sha(
    session: AsyncSession, vehicle_id: uuid.UUID, sha: str
) -> Optional[ObdLog]:
    """Returns the existing row for (vehicle, sha256), if any."""
    stmt = select(ObdLog).where(ObdLog.vehicle_id == vehicle_id, ObdLog.sha256 == sha)
    return (await session.execute(stmt)).scalar_one_or_none()


async def _describe_other_vehicle(
    session: AsyncSession, workshop_id: uuid.UUID, vin: str
) -> str:
    """Names the workshop vehicle that carries ``vin`` (for the 422 detail)."""
    stmt = select(Vehicle).where(
        Vehicle.workshop_id == workshop_id,
        Vehicle.vin == vin,
        Vehicle.deleted_at.is_(None),
    )
    other = (await session.execute(stmt)).scalar_one_or_none()
    if other is None:
        return f"file VIN {vin} is not registered in this workshop"
    label = other.plate or other.nickname or f"{other.manufacturer} {other.model}"
    return f"file VIN {vin} belongs to vehicle {other.id} ({label})"


async def store_log(
    session: AsyncSession,
    vehicle: Vehicle,
    data: bytes,
    original_filename: Optional[str],
    source: str,
    device_id: Optional[uuid.UUID] = None,
    uploaded_by: Optional[uuid.UUID] = None,
) -> Tuple[ObdLog, bool]:
    """Stores one uploaded log under ``vehicle``.

    Args:
        session: Async session.
        vehicle: Target vehicle (already authorised by the caller).
        data: Raw file bytes.
        original_filename: Client-supplied name (label only).
        source: ``"web"`` or ``"device"``.
        device_id: Device credential used (device uploads).
        uploaded_by: User id (web uploads).

    Returns:
        ``(row, duplicate)``; ``duplicate`` is True when the same bytes were
        already stored for this vehicle (nothing written).

    Raises:
        ApiError: 413 ``file_too_large``, 422 ``unsupported_format``,
            422 ``vin_mismatch``.
    """
    def _reject(status: int, code: str, message: str) -> ApiError:
        # PROD-07 FM-19: every refusal is one structured event so a device
        # that keeps getting rejected is visible server-side (the device's
        # own log is out of our reach).  ``first_line`` is enough to tell a
        # changed logger format from a wrong file.
        log.warning(
            "ingest.rejected",
            reason=code, status=status, source=source,
            vehicle_id=str(vehicle.id), device_id=str(device_id) if device_id else None,
            uploaded_by=str(uploaded_by) if uploaded_by else None,
            filename=original_filename, size_bytes=len(data),
            first_line=data[:120].split(b"\n", 1)[0].decode("utf-8", "replace"),
        )
        return ApiError(status, code, message)

    if len(data) > settings.max_upload_bytes:
        raise _reject(
            413, "file_too_large",
            f"File exceeds {settings.max_upload_bytes} bytes",
        )
    meta = sniff(data)
    if meta is None:
        raise _reject(
            422, "unsupported_format",
            "Only Jetson TSV and Yamaha dual-channel CSV logs are accepted",
        )
    sha = sha256_hex(data)
    existing = await find_by_sha(session, vehicle.id, sha)
    if existing is not None:
        log.info("ingest.duplicate", vehicle_id=str(vehicle.id), log_id=str(existing.id))
        return existing, True

    if meta.vin is not None and meta.vin != vehicle.vin:
        detail = await _describe_other_vehicle(session, vehicle.workshop_id, meta.vin)
        log.warning(
            "ingest.vin_mismatch",
            vehicle_id=str(vehicle.id), vehicle_vin=vehicle.vin,
            vin_from_log=meta.vin, source=source, filename=original_filename,
        )
        raise _reject(
            422, "vin_mismatch",
            f"Log VIN {meta.vin} does not match vehicle VIN {vehicle.vin}; {detail}",
        )

    store = storage()
    log_id = uuid.uuid4()
    raw_path = store.relative_path(vehicle.id, log_id, meta.extension)
    store.write(raw_path, data)
    row = ObdLog(
        id=log_id,
        vehicle_id=vehicle.id,
        sha256=sha,
        raw_path=raw_path,
        original_filename=(original_filename or "")[:255] or None,
        source=source,
        device_id=device_id,
        uploaded_by=uploaded_by,
        format=meta.format,
        size_bytes=len(data),
        vin_from_log=meta.vin,
        vin_mismatch=False,
        recorded_start=meta.recorded_start,
        recorded_end=meta.recorded_end,
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        # Lost a race with an identical concurrent upload: keep theirs.
        await session.rollback()
        store.delete(raw_path)
        existing = await find_by_sha(session, vehicle.id, sha)
        if existing is None:  # pragma: no cover - not a sha race after all
            raise
        return existing, True
    await session.refresh(row)
    log.info(
        "ingest.stored",
        vehicle_id=str(vehicle.id), log_id=str(row.id), format=row.format,
        size_bytes=row.size_bytes, source=source,
        vin_from_log=row.vin_from_log,
    )
    return row, False


async def list_logs(
    session: AsyncSession, vehicle_id: uuid.UUID, limit: int = 50, offset: int = 0
) -> List[ObdLog]:
    """Lists a vehicle's logs, newest upload first."""
    stmt = (
        select(ObdLog)
        .where(ObdLog.vehicle_id == vehicle_id)
        .order_by(ObdLog.uploaded_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list((await session.execute(stmt)).scalars())


async def get_log(session: AsyncSession, log_id: uuid.UUID) -> Optional[ObdLog]:
    """Returns a log row by id (caller authorises via its vehicle)."""
    return await session.get(ObdLog, log_id)
