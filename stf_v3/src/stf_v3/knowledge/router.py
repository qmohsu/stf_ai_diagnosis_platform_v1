"""Manual library endpoints (PROD-06): public library, manager-only writes.

Author: Xiangzhu Yan
"""

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from stf_v3.auth.manager import current_user
from stf_v3.auth.models import User
from stf_v3.db import get_session
from stf_v3.knowledge import service
from stf_v3.knowledge.schemas import ManualOut, ManualSearchOut, ManualTocOut

router = APIRouter(prefix="/v3", tags=["knowledge"])


@router.get("/manuals", response_model=List[ManualOut])
async def list_manuals(
    q: Optional[str] = Query(default=None, max_length=100),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> List[ManualOut]:
    """Lists every manual in the public library (any workshop member)."""
    await service.require_member(session, user.id)
    return [service.to_out(r) for r in await service.list_manuals(session, q)]


@router.get("/manuals/search", response_model=ManualSearchOut)
async def search_manuals(
    manual_id: uuid.UUID,
    q: str = Query(min_length=1, max_length=200),
    max_hits: int = Query(default=20, ge=1, le=100),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> ManualSearchOut:
    """Literal full-text search in one manual (the agent's absence check)."""
    await service.require_member(session, user.id)
    row = await service.get_manual(session, manual_id)
    hits, total, index_track = service.search_text(row, q, max_hits)
    return ManualSearchOut(
        manual_id=row.id, query=q, index_track=index_track, total_hits=total, hits=hits
    )


@router.get("/manuals/{manual_id}", response_model=ManualOut)
async def get_manual(
    manual_id: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> ManualOut:
    """Status and ingest progress of one manual."""
    await service.require_member(session, user.id)
    return service.to_out(await service.get_manual(session, manual_id))


@router.get("/manuals/{manual_id}/toc", response_model=ManualTocOut)
async def get_manual_toc(
    manual_id: uuid.UUID,
    max_depth: int = Query(default=3, ge=1, le=99),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> ManualTocOut:
    """Heading tree of one manual (index track when available)."""
    await service.require_member(session, user.id)
    row = await service.get_manual(session, manual_id)
    toc, index_track = service.toc_text(row, max_depth)
    return ManualTocOut(manual_id=row.id, index_track=index_track, max_depth=max_depth, toc=toc)


@router.post("/manuals", response_model=ManualOut, status_code=status.HTTP_201_CREATED)
async def upload_manual(
    file: UploadFile = File(...),
    manufacturer: str = Form(min_length=1, max_length=100),
    vehicle_model: str = Form(min_length=1, max_length=100),
    factory_code: Optional[str] = Form(default=None, max_length=100),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> ManualOut:
    """Manager upload: registers the PDF and queues the ingest job (201)."""
    await service.require_manager(session, user.id)
    data = await file.read()
    row = await service.upload_manual(
        session, data, file.filename or "manual.pdf", manufacturer, vehicle_model,
        factory_code, uploaded_by=user.id,
    )
    return service.to_out(row)


@router.delete("/manuals/{manual_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_manual(
    manual_id: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Manager delete: cancels the job, removes row and files (seed: 403)."""
    await service.require_manager(session, user.id)
    row = await service.get_manual(session, manual_id)
    await service.delete_manual(session, row, by=user.id)
