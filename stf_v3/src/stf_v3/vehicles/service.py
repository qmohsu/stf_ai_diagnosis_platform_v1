"""Vehicle services incl. the single authorization entry point
``can_access_vehicle()`` (design doc §1.8 coding convention).

Author: Xiangzhu Yan
"""

import hashlib
import secrets
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from stf_v3.errors import ApiError, forbidden, not_found
from stf_v3.vehicles.models import Vehicle, VehicleDevice
from stf_v3.vehicles.schemas import VehicleIn, VehiclePatch
from stf_v3.workshops import service as workshops


async def can_access_vehicle(
    session: AsyncSession,
    user_id: uuid.UUID,
    vehicle_id: uuid.UUID,
    roles: Tuple[str, ...] = ("manager", "technician"),
) -> Vehicle:
    """THE authorization check for anything vehicle-scoped.

    Stage 1 rule: the caller must be a member of the vehicle's workshop.
    Future fine-grained permissions change only this function.

    Args:
        session: Async session.
        user_id: Caller.
        vehicle_id: Target vehicle.
        roles: Accepted roles in the vehicle's workshop.

    Returns:
        The (non-deleted) vehicle.

    Raises:
        ApiError: 404 when the vehicle does not exist or the caller is not a
            member (existence is never leaked); 403 on insufficient role.
    """
    stmt = select(Vehicle).where(
        Vehicle.id == vehicle_id, Vehicle.deleted_at.is_(None)
    )
    vehicle = (await session.execute(stmt)).scalar_one_or_none()
    if vehicle is None:
        raise not_found("vehicle_not_found", "Vehicle not found")
    role = await workshops.role_in_workshop(session, user_id, vehicle.workshop_id)
    if role is None:
        raise not_found("vehicle_not_found", "Vehicle not found")
    if role not in roles:
        raise forbidden("role_required", f"Requires role in {roles}")
    return vehicle


async def list_vehicles(
    session: AsyncSession,
    workshop_id: uuid.UUID,
    q: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[Vehicle]:
    """Lists non-deleted vehicles of a workshop, optionally filtered."""
    stmt = select(Vehicle).where(
        Vehicle.workshop_id == workshop_id, Vehicle.deleted_at.is_(None)
    )
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Vehicle.vin.ilike(pattern),
                Vehicle.plate.ilike(pattern),
                Vehicle.nickname.ilike(pattern),
            )
        )
    stmt = stmt.order_by(Vehicle.created_at.desc()).limit(limit).offset(offset)
    return list((await session.execute(stmt)).scalars())


async def create_vehicle(
    session: AsyncSession, workshop_id: uuid.UUID, body: VehicleIn
) -> Vehicle:
    """Creates a vehicle record; VIN unique within the workshop.

    Raises:
        ApiError: 409 ``vin_exists``.
    """
    vehicle = Vehicle(workshop_id=workshop_id, **body.model_dump())
    session.add(vehicle)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise ApiError(409, "vin_exists", "VIN already registered in workshop")
    await session.refresh(vehicle)
    return vehicle


async def update_vehicle(
    session: AsyncSession, vehicle: Vehicle, body: VehiclePatch
) -> Vehicle:
    """Applies a partial update (VIN excluded by schema)."""
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(vehicle, key, value)
    vehicle.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(vehicle)
    return vehicle


async def soft_delete_vehicle(session: AsyncSession, vehicle: Vehicle) -> None:
    """Soft-deletes a vehicle; history stays."""
    vehicle.deleted_at = datetime.now(timezone.utc)
    await session.commit()


def new_device_token() -> str:
    """Returns a fresh device token (32 random bytes, URL-safe)."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Returns the sha256 hex digest stored for a device token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def create_device(
    session: AsyncSession,
    vehicle: Vehicle,
    label: str,
    created_by: uuid.UUID,
) -> Tuple[VehicleDevice, str]:
    """Creates a device credential; returns (row, plaintext token)."""
    token = new_device_token()
    device = VehicleDevice(
        vehicle_id=vehicle.id,
        label=label,
        token_hash=hash_token(token),
        created_by=created_by,
    )
    session.add(device)
    await session.commit()
    await session.refresh(device)
    return device, token


async def list_devices(
    session: AsyncSession, vehicle_id: uuid.UUID
) -> List[VehicleDevice]:
    """Lists device credentials of a vehicle (incl. revoked)."""
    stmt = (
        select(VehicleDevice)
        .where(VehicleDevice.vehicle_id == vehicle_id)
        .order_by(VehicleDevice.created_at)
    )
    return list((await session.execute(stmt)).scalars())


async def get_device(
    session: AsyncSession, device_id: uuid.UUID
) -> Optional[VehicleDevice]:
    """Returns a device row by id, or None."""
    return await session.get(VehicleDevice, device_id)


async def revoke_device(session: AsyncSession, device: VehicleDevice) -> None:
    """Revokes a device credential (token stops working immediately)."""
    device.revoked_at = datetime.now(timezone.utc)
    await session.commit()


async def resolve_device_token(
    session: AsyncSession, token: str
) -> Optional[Tuple[VehicleDevice, Vehicle]]:
    """Resolves a presented device token to (device, vehicle).

    Used by ``POST /v3/ingest/device`` (PROD-05).  Updates ``last_seen_at``.

    Returns:
        (device, vehicle) for a valid, unrevoked token on a live vehicle;
        None otherwise.
    """
    stmt = (
        select(VehicleDevice, Vehicle)
        .join(Vehicle, Vehicle.id == VehicleDevice.vehicle_id)
        .where(
            VehicleDevice.token_hash == hash_token(token),
            VehicleDevice.revoked_at.is_(None),
            Vehicle.deleted_at.is_(None),
        )
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        return None
    device, vehicle = row
    device.last_seen_at = func.now()
    await session.commit()
    return device, vehicle
