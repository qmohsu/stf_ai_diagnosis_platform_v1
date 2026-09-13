"""Ingest endpoints (code design §3.3): upload, list, metadata, download.

Uploads never trigger diagnosis; the diagnosis endpoint (PROD-08) is an
explicit action on the vehicle page.

Author: Xiangzhu Yan
"""

import uuid
from typing import List, Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    Query,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from stf_v3.auth.manager import current_user
from stf_v3.auth.models import User
from stf_v3.db import get_session
from stf_v3.errors import ApiError, not_found
from stf_v3.ingest import service
from stf_v3.ingest.models import ObdLog
from stf_v3.ingest.schemas import ObdLogOut, UploadOut
from stf_v3.vehicles import service as vehicles

router = APIRouter(prefix="/v3", tags=["ingest"])

_MEDIA_TYPES = {"tsv": "text/tab-separated-values", "yamaha": "text/csv"}


def _upload_out(row: ObdLog, duplicate: bool) -> UploadOut:
    out = UploadOut.model_validate(row)
    out.duplicate = duplicate
    return out


async def _authorised_log(
    session: AsyncSession, user: User, log_id: uuid.UUID
) -> ObdLog:
    """Loads a log and authorises through its vehicle (404 hides both a
    missing log and a log of a vehicle the caller may not see)."""
    row = await service.get_log(session, log_id)
    if row is None:
        raise not_found("log_not_found", "Log not found")
    try:
        await vehicles.can_access_vehicle(session, user.id, row.vehicle_id)
    except ApiError as exc:
        if exc.status_code == 404:
            raise not_found("log_not_found", "Log not found") from exc
        raise
    return row


@router.post(
    "/vehicles/{vehicle_id}/logs",
    response_model=UploadOut,
    status_code=status.HTTP_201_CREATED,
    responses={200: {"model": UploadOut, "description": "Already stored (duplicate)"}},
)
async def upload_log(
    vehicle_id: uuid.UUID,
    file: UploadFile = File(...),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> UploadOut:
    """Member upload: stores the file under the vehicle (201; 200 if the
    same bytes were already stored)."""
    vehicle = await vehicles.can_access_vehicle(session, user.id, vehicle_id)
    data = await file.read()
    row, duplicate = await service.store_log(
        session, vehicle, data, file.filename, source="web", uploaded_by=user.id
    )
    out = _upload_out(row, duplicate)
    if duplicate:
        return Response(  # type: ignore[return-value]
            content=out.model_dump_json(), media_type="application/json",
            status_code=status.HTTP_200_OK,
        )
    return out


@router.post(
    "/ingest/device",
    response_model=UploadOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        200: {"model": UploadOut, "description": "Already stored (duplicate)"},
        401: {"description": "Invalid or revoked device token"},
    },
)
async def upload_from_device(
    file: UploadFile = File(...),
    x_device_token: Optional[str] = Header(default=None, alias="X-Device-Token"),
    session: AsyncSession = Depends(get_session),
) -> UploadOut:
    """Device upload (D8): the token decides the vehicle; no user login."""
    if not x_device_token:
        raise ApiError(401, "device_token_required", "X-Device-Token header required")
    resolved = await vehicles.resolve_device_token(session, x_device_token)
    if resolved is None:
        raise ApiError(401, "device_token_invalid", "Device token invalid or revoked")
    device, vehicle = resolved
    data = await file.read()
    row, duplicate = await service.store_log(
        session, vehicle, data, file.filename, source="device", device_id=device.id
    )
    out = _upload_out(row, duplicate)
    if duplicate:
        return Response(  # type: ignore[return-value]
            content=out.model_dump_json(), media_type="application/json",
            status_code=status.HTTP_200_OK,
        )
    return out


@router.get("/vehicles/{vehicle_id}/logs", response_model=List[ObdLogOut])
async def list_logs(
    vehicle_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> List[ObdLogOut]:
    """Lists a vehicle's logs, newest first (members only)."""
    await vehicles.can_access_vehicle(session, user.id, vehicle_id)
    rows = await service.list_logs(session, vehicle_id, limit, offset)
    return [ObdLogOut.model_validate(r) for r in rows]


@router.get("/logs/{log_id}", response_model=ObdLogOut)
async def get_log(
    log_id: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> ObdLogOut:
    """Returns one log's metadata."""
    row = await _authorised_log(session, user, log_id)
    return ObdLogOut.model_validate(row)


@router.get("/logs/{log_id}/raw", response_class=FileResponse)
async def download_log(
    log_id: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> FileResponse:
    """Streams the original bytes back (filename = original upload name)."""
    row = await _authorised_log(session, user, log_id)
    store = service.storage()
    if not store.exists(row.raw_path):
        raise ApiError(500, "raw_file_missing", "Stored file is missing on disk")
    ext = "tsv" if row.format == "tsv" else "csv"
    return FileResponse(
        path=store.absolute_path(row.raw_path),
        media_type=_MEDIA_TYPES.get(row.format, "application/octet-stream"),
        filename=row.original_filename or f"{row.id}.{ext}",
    )
