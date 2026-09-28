#!/usr/bin/env python3
"""Fails when a tracked file holds a VIN-shaped string that is not a known fake.

PROD-15A T-27 / FM-52: this repository is PUBLIC.  Real VINs may live in the
backend's storage (APP-54) but never in Git.  Every git-tracked text file is
scanned for 17-character ISO 3779 shapes (no I, O, Q) that mix letters and
digits; only the allow-listed fakes pass.  Matches are printed masked
(``JHM…`` + a hash) so the CI log (public too) never repeats a real one.

    python3 stf_v3/scripts/check_no_vins.py            # exit 1 on any finding

Author: Xiangzhu Yan
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List, Tuple

ALLOWED = {
    "JHMGK5830HX202404": "project fake (CLAUDE.md)",
    "1HGCM82633A123456": "project fake (CLAUDE.md)",
    "1HGBH41JXMN109186": "public ISO 3779 textbook example (Wikipedia), used by an obd_agent test",
}
VIN_SHAPE = re.compile(r"(?<![A-Za-z0-9])[A-HJ-NPR-Z0-9]{17}(?![A-Za-z0-9])")


def vin_like(token: str) -> bool:
    """A 17-character VIN shape with both letters and digits (timestamps /
    numeric ids and all-letter words are not VINs)."""
    return (bool(VIN_SHAPE.fullmatch(token)) and any(c.isdigit() for c in token)
            and any(c.isalpha() for c in token))


def mask(token: str) -> str:
    """Enough to find it again, not enough to read it."""
    return f"{token[:3]}…#{hashlib.sha256(token.encode()).hexdigest()[:8]}"


def scan_text(text: str) -> List[Tuple[int, str]]:
    """(line number, masked token) for every non-allowed VIN-like string."""
    found = []
    for n, line in enumerate(text.splitlines(), 1):
        for token in VIN_SHAPE.findall(line):
            if vin_like(token) and token not in ALLOWED:
                found.append((n, mask(token)))
    return found


def tracked_files(root: Path) -> List[Path]:
    out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=True)
    return [root / p for p in out.stdout.decode().split("\0") if p]


def scan_files(paths: Iterable[Path], root: Path) -> List[str]:
    """``path:line: masked`` findings across text files (binary files skipped)."""
    findings = []
    for path in paths:
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"\0" in data[:8192]:
            continue
        for n, masked in scan_text(data.decode("utf-8", errors="replace")):
            findings.append(f"{path.relative_to(root).as_posix()}:{n}: VIN-shaped {masked}")
    return findings


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    findings = scan_files(tracked_files(root), root)
    for f in findings:
        print(f)
    if findings:
        print(f"{len(findings)} VIN-shaped string(s) outside the allow-list — use a fake VIN "
              f"({', '.join(k for k, v in ALLOWED.items() if 'project' in v)}) instead (FM-52).")
        return 1
    print("no real VINs in tracked files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
