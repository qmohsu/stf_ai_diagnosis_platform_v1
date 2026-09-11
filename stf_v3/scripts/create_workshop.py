#!/usr/bin/env python3
"""Creates a workshop and prints invite codes (Stage 1 has no admin UI).

Usage::

    STF_V3_DATABASE_URL=... python scripts/create_workshop.py \
        --name "Towngas Workshop" --manager-codes 1 --technician-codes 3

Prints one code per line as ``<role> <code>``.  Codes are shown once.

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

from stf_v3.db import SessionLocal  # noqa: E402
from stf_v3.workshops import service  # noqa: E402
from stf_v3.workshops.models import Workshop  # noqa: E402


async def _run(name: str, managers: int, technicians: int) -> None:
    """Creates (or reuses) the workshop and issues codes."""
    async with SessionLocal() as session:
        existing = (
            await session.execute(select(Workshop).where(Workshop.name == name))
        ).scalar_one_or_none()
        workshop = existing or await service.create_workshop(session, name)
        print(f"workshop {workshop.id} {'(existing)' if existing else '(created)'}")
        for role, count in (("manager", managers), ("technician", technicians)):
            if count:
                rows = await service.issue_invite_codes(
                    session, workshop.id, role, count, created_by=None
                )
                for row in rows:
                    print(f"{role} {row.code}")


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--manager-codes", type=int, default=1)
    parser.add_argument("--technician-codes", type=int, default=0)
    args = parser.parse_args(argv)
    asyncio.run(_run(args.name, args.manager_codes, args.technician_codes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
