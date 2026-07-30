"""One-command manual onboarding (runbook steps 1-4, S4.1).

Runs the full pipeline for one uploaded PDF and installs the
artifacts into the manuals volume layout::

    <volume>/<Model Dir>/index/{<id>.md, <id>.index.yaml, images/}

Designed to be invoked by ``marker_worker`` as the *upgrade stage*
after its quick marker conversion (two-stage onboarding: the
manual is usable on the legacy track within minutes; this pipeline
upgrades it to the index track when done), or manually per the
runbook.

Usage (build host)::

    PYTHONPATH=<repo>/diagnostic_api \\
    <venv-audit>/bin/python -m manual_pipeline.onboard \\
        --pdf <volume>/uploads/<id>.pdf \\
        --model-dir "<volume>/<Model Dir>" \\
        [--work-dir ~/manual_builds] [--skip-convert]

Identity (manufacturer / vehicle_model) is read from the legacy
markdown's frontmatter in ``--model-dir`` when present.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

_MINERU_BIN = os.environ.get(
    "MINERU_BIN",
    os.path.expanduser("~/bakeoff/venv-mineru/bin/mineru"),
)
_MINERU_TIMEOUT_S = 3600


def _identity_from_model_dir(
    model_dir: Path, manual_id: str,
) -> dict:
    """Manufacturer/model from the legacy md frontmatter."""
    legacy = model_dir / f"{manual_id}.md"
    out = {"manufacturer": "", "vehicle_model": ""}
    if legacy.is_file():
        text = legacy.read_text(
            encoding="utf-8", errors="ignore",
        ).lstrip()
        if text.startswith("---"):
            end = text.find("---", 3)
            if end > 0:
                try:
                    fm = yaml.safe_load(text[3:end]) or {}
                    out["manufacturer"] = str(
                        fm.get("manufacturer") or "",
                    )
                    out["vehicle_model"] = str(
                        fm.get("vehicle_model") or "",
                    )
                except yaml.YAMLError:
                    pass
    return out


def run_onboard(
    pdf: Path,
    model_dir: Path,
    work_dir: Path,
    skip_convert: bool = False,
) -> bool:
    """Convert → build → index → install for one manual.

    Returns:
        True when every gate passed and artifacts are installed.
    """
    manual_id = pdf.stem
    work = work_dir / manual_id
    work.mkdir(parents=True, exist_ok=True)
    mineru_out = work / "mineru"

    # ── 1. Geometry conversion (MinerU) ──────────────────────
    if not skip_convert or not list(
        mineru_out.rglob("*_content_list_v2.json"),
    ):
        print(f"[onboard] converting {pdf.name} …", flush=True)
        subprocess.run(
            [_MINERU_BIN, "-p", str(pdf), "-o",
             str(mineru_out), "-b", "hybrid-engine"],
            check=True, timeout=_MINERU_TIMEOUT_S,
        )
    engine_dir = next(
        p.parent for p in mineru_out.rglob(
            "*_content_list_v2.json",
        )
    )

    # ── 2+3. Storage build + index build (in-process) ────────
    from manual_pipeline.build import run_build
    from manual_pipeline.index_build import run_index_build

    identity = _identity_from_model_dir(model_dir, manual_id)
    fm_path = work / "frontmatter.md"
    fm_path.write_text(
        "---\n"
        f"manufacturer: {identity['manufacturer']}\n"
        f"vehicle_model: {identity['vehicle_model']}\n"
        "---\n",
        encoding="utf-8",
    )
    out_dir = work / "out"
    run_build(pdf, engine_dir, out_dir, fm_path)  # raises on I0

    models = [
        m for m in (identity["vehicle_model"],) if m
    ]
    # Rebuilds reuse prior summaries from the deployed sidecar
    # (stable node_ids make them transferable — zero re-spend).
    prior_sidecar = (
        model_dir / "index" / f"{manual_id}.index.yaml"
    )
    ok = run_index_build(
        engine_dir,
        out_dir / f"{manual_id}.md",
        manual_id,
        out_dir,
        applicability=(
            {"manufacturer": identity["manufacturer"],
             "models": models}
            if identity["manufacturer"] and models else None
        ),
        with_summaries=True,
        model=os.environ.get(
            "SUMMARY_MODEL", "deepseek/deepseek-v3.2",
        ),
        item_lines_path=out_dir / "item_lines.json",
        reuse_summaries_from=(
            prior_sidecar if prior_sidecar.is_file() else None
        ),
    )
    if not ok:
        print("[onboard] index gates FAILED — not installing",
              file=sys.stderr)
        return False

    # ── 4. Install into the volume layout ────────────────────
    index_dir = model_dir / "index"
    index_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(out_dir / f"{manual_id}.md", index_dir)
    shutil.copy2(
        out_dir / f"{manual_id}.index.yaml", index_dir,
    )
    dst_images = index_dir / "images"
    if dst_images.exists():
        shutil.rmtree(dst_images)
    shutil.copytree(out_dir / "images", dst_images)
    shutil.copy2(
        out_dir / "index_build_report.json",
        index_dir / "index_build_report.json",
    )
    print(f"[onboard] installed index track for {manual_id} "
          f"into {index_dir}", flush=True)
    return True


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument(
        "--model-dir", type=Path, required=True,
    )
    parser.add_argument(
        "--work-dir", type=Path,
        default=Path(os.path.expanduser("~/manual_builds")),
    )
    parser.add_argument(
        "--skip-convert", action="store_true", default=False,
    )
    args = parser.parse_args()
    try:
        ok = run_onboard(
            args.pdf, args.model_dir, args.work_dir,
            skip_convert=args.skip_convert,
        )
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"[onboard] FAILED: {exc}", file=sys.stderr)
        return 1
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
