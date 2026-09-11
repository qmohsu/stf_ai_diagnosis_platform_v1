#!/usr/bin/env python3
"""Exports the OpenAPI contract to ``docs/api/v3_openapi.json``.

CI regenerates and diffs this file (code design §10); a mismatch means the
API changed without the contract being updated.

Usage::

    python scripts/export_openapi.py [--out path] [--check]

Author: Xiangzhu Yan
"""

import argparse
import json
import os
import sys
from typing import List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "src"))
_DEFAULT_OUT = os.path.join(os.path.dirname(_ROOT), "docs", "api", "v3_openapi.json")


def main(argv: Optional[List[str]] = None) -> int:
    """Writes (or checks) the OpenAPI JSON.

    Returns:
        0 on success / match, 1 when ``--check`` finds a difference.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=_DEFAULT_OUT)
    parser.add_argument(
        "--check", action="store_true", help="compare instead of writing"
    )
    args = parser.parse_args(argv)

    from stf_v3.main import app  # noqa: E402 (path set above)

    spec = json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if args.check:
        with open(args.out, encoding="utf-8") as fh:
            current = fh.read()
        if current != spec:
            print(f"OpenAPI drift: {args.out} differs from the running app")
            return 1
        print("OpenAPI contract up to date")
        return 0
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(spec)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
