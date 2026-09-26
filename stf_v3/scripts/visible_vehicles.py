#!/usr/bin/env python3
"""Lists what one account can see: its workshops and their vehicles (PROD-11 FM-7).

Run after handing out an invite code (e.g. the test workshop for the
frontend student) to prove the account sees ONLY the intended workshop::

    STF_V3_DATABASE_URL=... python scripts/visible_vehicles.py --username student1

VINs are printed masked (last four characters) — raw VINs never go to a
terminal or transcript.

Author: Xiangzhu Yan
"""

import argparse
import asyncio
import os
import sys
from typing import List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from sqlalchemy import select  # noqa: E402

import stf_v3.metadata  # noqa: E402,F401  (registers every model for FK resolution)
from stf_v3.auth.models import User  # noqa: E402
from stf_v3.db import SessionLocal  # noqa: E402
from stf_v3.vehicles.models import Vehicle  # noqa: E402
from stf_v3.workshops.models import Membership, Workshop  # noqa: E402


def mask_vin(vin: str) -> str:
    """``*************2404`` — enough to tell vehicles apart, not the VIN."""
    return "*" * max(0, len(vin) - 4) + vin[-4:]


async def _run(username: str) -> int:
    async with SessionLocal() as session:
        user = (await session.execute(select(User).where(User.username == username))).scalar_one_or_none()
        if user is None:
            print(f"no such user: {username}")
            return 2
        rows = (await session.execute(
            select(Workshop, Membership.role).join(Membership, Membership.workshop_id == Workshop.id)
            .where(Membership.user_id == user.id)
        )).all()
        print(f"user {username}: {len(rows)} workshop(s)")
        for workshop, role in rows:
            vehicles = (await session.execute(
                select(Vehicle).where(Vehicle.workshop_id == workshop.id, Vehicle.deleted_at.is_(None))
            )).scalars().all()
            print(f"  workshop '{workshop.name}' ({role}): {len(vehicles)} vehicle(s)")
            for v in vehicles:
                print(f"    {v.manufacturer} {v.model}  vin {mask_vin(v.vin)}  plate {v.plate or '-'}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True)
    args = parser.parse_args(argv)
    return asyncio.run(_run(args.username))


if __name__ == "__main__":
    sys.exit(main())
