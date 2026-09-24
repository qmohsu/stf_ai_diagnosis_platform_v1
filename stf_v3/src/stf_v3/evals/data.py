"""Golden eval data: locate, verify (hashes) and load (PROD-10).

The data lives OUTSIDE the Python package, in ``stf_v3/evals/`` (copied into
the image as ``/app/evals``): the two locked golden sets, the road-test
fixture, ``MANIFEST.json`` (sha256 of every file, FM-8) and
``thresholds.yaml`` (baseline + gate rules).  Every run verifies the
manifest before starting; a changed byte refuses the run.

Author: Xiangzhu Yan
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from stf_v3.evals.schemas import GoldenEntry

LANE_FILES = {
    "manual_agent": "golden/manual_mws150a.jsonl",
    "obd_agent": "golden/obd_yamaha_road_test.jsonl",
}
FIXTURE_FILE = "fixtures/yamaha_road_test.csv"
MANIFEST = "MANIFEST.json"
THRESHOLDS = "thresholds.yaml"


class EvalDataError(RuntimeError):
    """The eval data directory is missing or does not match its manifest."""


def data_dir(explicit: Optional[str] = None) -> Path:
    """The eval data directory.

    Order: ``explicit`` → ``STF_V3_EVAL_DATA_DIR`` → ``./evals`` (the image's
    ``/app/evals``) → ``<repo>/stf_v3/evals`` relative to this file (dev).
    """
    candidates: List[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env = os.environ.get("STF_V3_EVAL_DATA_DIR")
    if env:
        candidates.append(Path(env))
    candidates.append(Path.cwd() / "evals")
    candidates.append(Path(__file__).resolve().parents[3] / "evals")
    for c in candidates:
        if (c / MANIFEST).is_file():
            return c
    raise EvalDataError(f"eval data directory not found (looked in: {', '.join(str(c) for c in candidates)})")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(root: Path) -> Dict[str, Any]:
    return json.loads((root / MANIFEST).read_text(encoding="utf-8"))


def verify(root: Path) -> Dict[str, str]:
    """Check every manifest file's sha256.

    Returns:
        ``{relative path: sha256}`` (recorded in the scorecard config).

    Raises:
        EvalDataError: A file is missing or its bytes changed.
    """
    manifest = load_manifest(root)
    out: Dict[str, str] = {}
    for rel, rec in manifest["files"].items():
        path = root / rel
        if not path.is_file():
            raise EvalDataError(f"eval data file missing: {rel}")
        digest = sha256_file(path)
        if digest != rec["sha256"]:
            raise EvalDataError(
                f"eval data file changed: {rel} (sha256 {digest[:12]} != manifest {rec['sha256'][:12]}); "
                "golden sets are never edited in place")
        out[rel] = digest
    return out


def load_golden(root: Path, lane: str) -> List[GoldenEntry]:
    """Parse one lane's golden JSONL into validated entries."""
    rel = LANE_FILES[lane]
    entries: List[GoldenEntry] = []
    with open(root / rel, encoding="utf-8") as fh:
        for n, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                entries.append(GoldenEntry.model_validate_json(line))
            except Exception as exc:  # noqa: BLE001
                raise EvalDataError(f"{rel} line {n} failed validation: {exc}") from exc
    return entries


def fixture_dir(root: Path) -> Path:
    return (root / FIXTURE_FILE).parent


def manual_hashes(manual_root: Path, manual_ids: List[str]) -> Dict[str, Dict[str, str]]:
    """sha256 of each manual's Markdown and index sidecar (FM-9 / FM-7).

    Looks the files up by manual id anywhere under the library root (the
    manual directory name is not the id).  Missing files are reported as
    ``"missing"`` rather than raising: the run itself will fail loudly if
    a manual it needs is absent.
    """
    out: Dict[str, Dict[str, str]] = {}
    for mid in manual_ids:
        rec: Dict[str, str] = {}
        for kind, pattern in (("md", f"{mid}.md"), ("index", f"{mid}.index.yaml")):
            found = next(iter(manual_root.rglob(pattern)), None) if manual_root.is_dir() else None
            rec[kind] = sha256_file(found) if found else "missing"
        out[mid] = rec
    return out


__all__ = ["EvalDataError", "FIXTURE_FILE", "LANE_FILES", "data_dir", "fixture_dir", "load_golden",
           "load_manifest", "manual_hashes", "sha256_file", "verify"]
