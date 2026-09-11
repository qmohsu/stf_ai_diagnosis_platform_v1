"""Vehicle and device endpoints (§3.2 of the code design).

Author: Xiangzhu Yan
"""

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from stf_v3.auth.manager import current_user
from stf_v3.auth.models import User
from stf_v3.db import get_session
from stf_v3.errors import not_found
from stf_v3.vehicles import service
from stf_v3.vehicles.schemas import (
    DeviceCreatedOut,
    DeviceIn,
    DeviceOut,
    VehicleIn,
    VehicleOut,
    VehiclePatch,
)
from stf_v3.workshops import service as workshops

router = APIRouter(prefix="/v3", tags=["vehicles"])


@router.get("/workshops/{workshop_id}/vehicles", response_model=List[VehicleOut])
async def list_vehicles(
    workshop_id: uuid.UUID,
    q: Optional[str] = Query(default=None, max_length=50),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> List[VehicleOut]:
    """Lists vehicles of a workshop; ``q`` matches VIN, plate or nickname."""
    await workshops.require_role(session, user.id, workshop_id)
    rows = await service.list_vehicles(session, workshop_id, q, limit, offset)
    return [VehicleOut.model_validate(v) for v in rows]


@router.post(
    "/workshops/{workshop_id}/vehicles",
    response_model=VehicleOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_vehicle(
    workshop_id: uuid.UUID,
    body: VehicleIn,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> VehicleOut:
    """Creates a vehicle record (any member)."""
    await workshops.require_role(session, user.id, workshop_id)
    vehicle = await service.create_vehicle(session, workshop_id, body)
    return VehicleOut.model_validate(vehicle)


@router.get("/vehicles/{vehicle_id}", response_model=VehicleOut)
async def get_vehicle(
    vehicle_id: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> VehicleOut:
    """Returns one vehicle (members of its workshop)."""
    vehicle = await service.can_access_vehicle(session, user.id, vehicle_id)
    return VehicleOut.model_validate(vehicle)


@router.patch("/vehicles/{vehicle_id}", response_model=VehicleOut)
async def patch_vehicle(
    vehicle_id: uuid.UUID,
    body: VehiclePatch,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> VehicleOut:
    """Updates plate / nickname / manufacturer / model.  VIN is immutable."""
    vehicle = await service.can_access_vehicle(session, user.id, vehicle_id)
    vehicle = await service.update_vehicle(session, vehicle, body)
    return VehicleOut.model_validate(vehicle)


@router.delete("/vehicles/{vehicle_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_vehicle(
    vehicle_id: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Soft-deletes a vehicle (manager only); history is kept."""
    vehicle = await service.can_access_vehicle(
        session, user.id, vehicle_id, roles=("manager",)
    )
    await service.soft_delete_vehicle(session, vehicle)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/vehicles/{vehicle_id}/devices",
    response_model=DeviceCreatedOut,
    status_code=status.HTTP_201_CREATED,
    tags=["devices"],
)
async def create_device(
    vehicle_id: uuid.UUID,
    body: DeviceIn,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> DeviceCreatedOut:
    """Issues a device credential for this vehicle (manager only).

    The plaintext ``token`` is returned once and never stored.
    """
    vehicle = await service.can_access_vehicle(
        session, user.id, vehicle_id, roles=("manager",)
    )
    device, token = await service.create_device(session, vehicle, body.label, user.id)
    return DeviceCreatedOut(**DeviceOut.model_validate(device).model_dump(), token=token)


@router.get(
    "/vehicles/{vehicle_id}/devices", response_model=List[DeviceOut], tags=["devices"]
)
async def list_devices(
    vehicle_id: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> List[DeviceOut]:
    """Lists device credentials of a vehicle (members)."""
    await service.can_access_vehicle(session, user.id, vehicle_id)
    return [DeviceOut.model_validate(d) for d in await service.list_devices(session, vehicle_id)]


@router.delete(
    "/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["devices"]
)
async def revoke_device(
    device_id: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Revokes a device credential (manager of the vehicle's workshop)."""
    device = await service.get_device(session, device_id)
    if device is None:
        raise not_found("device_not_found", "Device not found")
    await service.can_access_vehicle(
        session, user.id, device.vehicle_id, roles=("manager",)
    )
    await service.revoke_device(session, device)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
