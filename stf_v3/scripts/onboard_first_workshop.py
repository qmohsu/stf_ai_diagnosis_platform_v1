#!/usr/bin/env python3
"""Onboards a real workshop: invite codes, vehicles, one device token each.

PROD-07 ops step (dev plan §3.3).  Stage 1 has no admin UI, so the first
real workshop, its manager / technician invite codes, the vehicle records
and the per-vehicle device tokens are created here, directly against the
V3 database.

Privacy rules baked in (kickoff FM-23, FM-33, FM-22):

* VINs are never accepted as CLI arguments (shell history) and never
  printed; they are typed twice on a hidden prompt (or read from stdin
  when not a TTY) and must match, else nothing is written.
* After the write every VIN is read back from the database and compared
  in memory with what was typed.
* Device tokens are shown once — on stdout, or written as one env file
  per vehicle (mode 600) with ``--env-out-dir`` so they can be handed
  over per car without pasting into chat.

Usage::

    STF_V3_DATABASE_URL=... python scripts/onboard_first_workshop.py \
        --workshop "PolyU STF 实验车队" --manager-codes 1 --technician-codes 1 \
        --vehicle "Toyota|Hiace|<plate>|Hiace" --vehicle "Toyota|Corolla||Corolla" \
        --env-out-dir ~/stf_v3_tokens

Re-running is safe: an existing workshop (by name) and existing vehicles
(by VIN) are reused; only new invite codes and new device tokens are
issued.

Author: Xiangzhu Yan
"""

import argparse
import asyncio
import getpass
import os
import re
import sys
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

import stf_v3.metadata  # noqa: E402,F401  (registers every model for FK resolution)
from stf_v3.errors import ApiError  # noqa: E402
from stf_v3.vehicles import service as vehicles  # noqa: E402
from stf_v3.vehicles.models import Vehicle  # noqa: E402
from stf_v3.vehicles.schemas import VIN_PATTERN, VehicleIn  # noqa: E402
from stf_v3.workshops import service as workshops  # noqa: E402
from stf_v3.workshops.models import Workshop  # noqa: E402

_VIN_RE = re.compile(VIN_PATTERN)


class OnboardError(Exception):
    """Input problem found before anything was written."""


@dataclass(frozen=True)
class VehicleSpec:
    """One ``--vehicle`` argument: ``manufacturer|model|plate|nickname``."""

    manufacturer: str
    model: str
    plate: Optional[str]
    nickname: Optional[str]

    @classmethod
    def parse(cls, raw: str) -> "VehicleSpec":
        """Parses the pipe-separated form (plate / nickname optional)."""
        parts = [p.strip() for p in raw.split("|")]
        parts += [""] * (4 - len(parts))
        manufacturer, model, plate, nickname = parts[:4]
        if not manufacturer or not model:
            raise OnboardError(f"--vehicle needs at least manufacturer|model: {raw!r}")
        return cls(manufacturer, model, plate or None, nickname or None)

    def label(self) -> str:
        return f"{self.manufacturer} {self.model}" + (f" ({self.plate})" if self.plate else "")


def read_vin_twice(label: str, reader: Callable[[str], str]) -> str:
    """Asks for a VIN twice; both entries must match and be a valid VIN.

    Raises:
        OnboardError: Mismatch or invalid VIN (nothing is written).
    """
    first = reader(f"VIN for {label}: ").strip().upper()
    second = reader(f"VIN for {label} (again): ").strip().upper()
    if first != second:
        raise OnboardError(f"VIN entries for {label} do not match")
    if not _VIN_RE.fullmatch(first):
        raise OnboardError(f"VIN for {label} is not 17 chars A-Z/0-9 without I, O, Q")
    return first


def default_reader(prompt: str) -> str:
    """Hidden prompt on a TTY; one line from stdin otherwise (tests, pipes)."""
    if sys.stdin.isatty():
        return getpass.getpass(prompt)
    line = sys.stdin.readline()
    if not line:
        raise OnboardError("stdin ended before every VIN was supplied")
    return line


def collect_vins(specs: List[VehicleSpec], reader: Callable[[str], str]) -> List[str]:
    """Collects one VIN per spec (all up front, so a mismatch aborts early)."""
    return [read_vin_twice(spec.label(), reader) for spec in specs]


async def onboard(
    session: AsyncSession,
    workshop_name: str,
    manager_codes: int,
    technician_codes: int,
    specs: List[VehicleSpec],
    vins: List[str],
    device_label: str,
) -> Dict[str, object]:
    """Creates / reuses the workshop, issues codes, creates vehicles + tokens.

    Returns:
        ``{"workshop_id", "created", "codes": [(role, code)],
        "vehicles": [{"id", "label", "existing", "token", "vin_ok"}]}``.
        VINs are not part of the result.
    """
    existing_ws = (
        await session.execute(select(Workshop).where(Workshop.name == workshop_name))
    ).scalar_one_or_none()
    workshop = existing_ws or await workshops.create_workshop(session, workshop_name)
    result: Dict[str, object] = {
        "workshop_id": str(workshop.id),
        "created": existing_ws is None,
        "codes": [],
        "vehicles": [],
    }
    for role, count in (("manager", manager_codes), ("technician", technician_codes)):
        if count:
            rows = await workshops.issue_invite_codes(session, workshop.id, role, count, None)
            result["codes"].extend((role, r.code) for r in rows)  # type: ignore[attr-defined]

    for spec, vin in zip(specs, vins):
        body = VehicleIn(
            vin=vin, manufacturer=spec.manufacturer, model=spec.model,
            plate=spec.plate, nickname=spec.nickname,
        )
        existing = False
        try:
            vehicle = await vehicles.create_vehicle(session, workshop.id, body)
        except ApiError as exc:
            if exc.code != "vin_exists":
                raise
            existing = True
            vehicle = (
                await session.execute(
                    select(Vehicle).where(
                        Vehicle.workshop_id == workshop.id, Vehicle.vin == vin,
                        Vehicle.deleted_at.is_(None),
                    )
                )
            ).scalar_one()
        device, token = await vehicles.create_device(
            session, vehicle, f"{device_label} {spec.model}", created_by=None
        )
        # FM-33: read back from the database and compare in memory only.
        session.expire(vehicle)
        fresh = await session.get(Vehicle, vehicle.id)
        vin_ok = fresh is not None and fresh.vin == vin
        result["vehicles"].append({  # type: ignore[attr-defined]
            "id": str(vehicle.id), "label": spec.label(), "existing": existing,
            "device_id": str(device.id), "token": token, "vin_ok": vin_ok,
        })
    return result


def env_file_text(base_url: str, vehicle_id: str, token: str, label: str) -> str:
    """The V3 env file the uploader on this vehicle's device reads."""
    return (
        f"# STF V3 uploader config for {label} — keep mode 600, never commit\n"
        f"STF_V3_BASE_URL={base_url}\n"
        f"STF_V3_DEVICE_TOKEN={token}\n"
        f"STF_V3_VEHICLE_ID={vehicle_id}\n"
    )


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower() or "vehicle"


def report(result: Dict[str, object], base_url: str, env_out_dir: Optional[str]) -> int:
    """Prints the outcome; writes per-vehicle env files when asked.

    Returns:
        0 when every VIN read back correctly, else 1.
    """
    print(f"workshop {result['workshop_id']} {'(created)' if result['created'] else '(existing)'}")
    for role, code in result["codes"]:  # type: ignore[union-attr]
        print(f"invite {role} {code}")
    rc = 0
    for v in result["vehicles"]:  # type: ignore[union-attr]
        state = "existing" if v["existing"] else "created"
        print(f"vehicle {v['id']} {v['label']} ({state}) device {v['device_id']} "
              f"VIN readback {'OK' if v['vin_ok'] else 'MISMATCH'}")
        if not v["vin_ok"]:
            rc = 1
        text = env_file_text(base_url, v["id"], v["token"], v["label"])
        if env_out_dir:
            os.makedirs(env_out_dir, mode=0o700, exist_ok=True)
            path = os.path.join(env_out_dir, f"v3_uploader_{_slug(v['label'])}.env")
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            print(f"  env file written: {path} (token inside, shown nowhere else)")
        else:
            print("  --- env file for this vehicle (token shown once) ---")
            print(text, end="")
    return rc


async def _run(args: argparse.Namespace) -> int:
    from stf_v3.db import SessionLocal

    specs = [VehicleSpec.parse(v) for v in args.vehicle]
    vins = collect_vins(specs, default_reader)          # aborts before any write
    async with SessionLocal() as session:
        result = await onboard(
            session, args.workshop, args.manager_codes, args.technician_codes,
            specs, vins, args.device_label,
        )
    return report(result, args.base_url, args.env_out_dir)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--workshop", required=True, help="Workshop name (reused if it exists).")
    parser.add_argument("--manager-codes", type=int, default=1)
    parser.add_argument("--technician-codes", type=int, default=1)
    parser.add_argument(
        "--vehicle", action="append", default=[],
        help="manufacturer|model|plate|nickname (plate/nickname optional); repeatable. "
             "The VIN is asked for interactively, twice.",
    )
    parser.add_argument("--device-label", default="Jetson")
    parser.add_argument("--base-url", default="https://stf-diagnosis.dev",
                        help="V3 base URL written into the env files.")
    parser.add_argument("--env-out-dir", default=None,
                        help="Write one env file per vehicle here (mode 600) instead of printing tokens.")
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except OnboardError as exc:
        print(f"ERROR {exc} — nothing written", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
