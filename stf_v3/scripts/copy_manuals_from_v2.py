#!/usr/bin/env python3
"""One-off copy of the V2 manual library into V3 (PROD-06, S1).

Reads a JSON export of V2's ``manuals`` rows (produced with the container's
own psql — V2 credentials never reach V3, FM-35) plus the V2 volume path,
copies each manual's WHOLE directory (Markdown, images, index sidecars,
build report) and its source PDF into the V3 volume with the directory
structure unchanged, then registers the manual in V3 as a seed
(``uploaded_by`` NULL, status ``ingested``).  Idempotent: an existing
``file_hash`` is skipped; ``--verify`` re-checks every file by sha256
(FM-1) and that every registered path lies inside the V3 root (FM-2).

Usage (on the server)::

    podman exec stf-postgres psql -U stf_user -d stf_diagnosis -tA -c \\
      "SELECT json_agg(m) FROM manuals m" > /tmp/v2_manuals.json
    python scripts/copy_manuals_from_v2.py --export /tmp/v2_manuals.json \\
      --src <v1 volume _data> --dst <v3 volume _data> \\
      --database-url <owner url>          # copy + register
    python scripts/copy_manuals_from_v2.py ... --verify   # compare only

Author: Xiangzhu Yan
"""

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))


def sha256_file(path: Path) -> str:
    """Streaming sha256 of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def manual_dir_of(row: Dict[str, str]) -> Optional[str]:
    """The V2 manual directory (first path component of md_file_path)."""
    md = row.get("md_file_path") or ""
    return md.split("/", 1)[0] if "/" in md else None


def plan(rows: List[Dict[str, str]], src: Path) -> List[Tuple[Dict[str, str], Path, Path]]:
    """Returns (row, manual dir, pdf) triples that exist in the V2 volume."""
    out = []
    for row in rows:
        d = manual_dir_of(row)
        if not d:
            print(f"skip {row.get('id')}: no md_file_path")
            continue
        mdir = src / d
        pdf = src / (row.get("pdf_file_path") or f"uploads/{row['id']}.pdf")
        if not mdir.is_dir():
            print(f"skip {row['id']}: dir {mdir} missing")
            continue
        out.append((row, mdir, pdf))
    return out


def copy_tree(src_dir: Path, dst_dir: Path) -> int:
    """Copies a directory tree (resume-safe); returns files copied."""
    n = 0
    for p in src_dir.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(src_dir)
        target = dst_dir / rel
        if target.is_file() and target.stat().st_size == p.stat().st_size and sha256_file(target) == sha256_file(p):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".part")
        shutil.copy2(p, tmp)
        os.replace(tmp, target)
        n += 1
    return n


def verify_tree(src_dir: Path, dst_dir: Path) -> Tuple[int, List[str]]:
    """Compares every file by sha256; returns (checked, problems)."""
    problems: List[str] = []
    checked = 0
    for p in src_dir.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(src_dir)
        target = dst_dir / rel
        checked += 1
        if not target.is_file():
            problems.append(f"missing {rel}")
        elif sha256_file(target) != sha256_file(p):
            problems.append(f"hash mismatch {rel}")
    return checked, problems


def index_content_file(mdir: Path, manual_id: str) -> Optional[str]:
    """Path (relative to the manual dir) of the index-track content md."""
    sidecar = mdir / "index" / f"{manual_id}.index.yaml"
    if not sidecar.is_file():
        return None
    import yaml

    raw = yaml.safe_load(sidecar.read_text(encoding="utf-8"))
    name = (raw.get("source") or {}).get("content_file")
    return f"index/{name}" if name else None


def register(rows_done: List[Tuple[Dict[str, str], str, Optional[str]]], database_url: str) -> Tuple[int, int]:
    """Inserts seed rows into V3 (skips existing file_hash). Returns (inserted, skipped)."""
    from sqlalchemy import create_engine, text

    import stf_v3.metadata  # noqa: F401 - full metadata for FK resolution

    eng = create_engine(database_url)
    inserted = skipped = 0
    with eng.begin() as conn:
        for row, mdir_name, content_rel in rows_done:
            exists = conn.execute(text("SELECT 1 FROM manuals WHERE file_hash = :h"), {"h": row["file_hash"]}).first()
            if exists:
                skipped += 1
                continue
            md_path = f"{mdir_name}/{content_rel}" if content_rel else row["md_file_path"]
            conn.execute(text(
                "INSERT INTO manuals (id, uploaded_by, filename, file_hash, manufacturer, vehicle_model, "
                "factory_code, status, file_size_bytes, page_count, section_count, language, converter, "
                "md_file_path, pdf_file_path, pages_processed, pages_total, warnings) VALUES "
                "(:id, NULL, :filename, :file_hash, :manufacturer, :vehicle_model, :factory_code, 'ingested', "
                ":size, :pages, :sections, :language, :converter, :md, :pdf, 6, 6, :warnings)"
            ), {
                "id": row["id"], "filename": row["filename"], "file_hash": row["file_hash"],
                "manufacturer": row["manufacturer"], "vehicle_model": row["vehicle_model"],
                "factory_code": row.get("factory_code"), "size": row["file_size_bytes"],
                "pages": row.get("page_count"), "sections": row.get("section_count"),
                "language": row.get("language"),
                "converter": (row.get("converter") or "") + " (copied from V2)",
                "md": md_path, "pdf": row.get("pdf_file_path") or f"uploads/{row['id']}.pdf",
                "warnings": json.dumps({"copied_from_v2": True, "index_track": bool(content_rel)}),
            })
            inserted += 1
    return inserted, skipped


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", required=True, help="JSON array of V2 manuals rows")
    ap.add_argument("--src", required=True, type=Path, help="V1/V2 manuals volume _data dir")
    ap.add_argument("--dst", required=True, type=Path, help="V3 manuals volume _data dir")
    ap.add_argument("--database-url", default=os.environ.get("STF_V3_DATABASE_URL", ""))
    ap.add_argument("--verify", action="store_true", help="compare only, no copy/insert")
    args = ap.parse_args(argv)

    rows = json.loads(Path(args.export).read_text(encoding="utf-8")) or []
    src, dst = args.src.resolve(), args.dst.resolve()
    todo = plan(rows, src)
    print(f"V2 rows: {len(rows)}  copyable: {len(todo)}")
    problems: List[str] = []
    done: List[Tuple[Dict[str, str], str, Optional[str]]] = []
    total_files = 0
    for row, mdir, pdf in todo:
        dst_dir = dst / mdir.name
        if not args.verify:
            n = copy_tree(mdir, dst_dir)
            dst_pdf = dst / "uploads" / pdf.name
            if pdf.is_file():
                dst_pdf.parent.mkdir(parents=True, exist_ok=True)
                if not (dst_pdf.is_file() and sha256_file(dst_pdf) == sha256_file(pdf)):
                    shutil.copy2(pdf, dst_pdf.with_name(dst_pdf.name + ".part"))
                    os.replace(dst_pdf.with_name(dst_pdf.name + ".part"), dst_pdf)
                    n += 1
            print(f"copied {row['id']} ({mdir.name}): {n} new file(s)")
        checked, probs = verify_tree(mdir, dst_dir)
        if pdf.is_file():
            checked += 1
            dst_pdf = dst / "uploads" / pdf.name
            if not dst_pdf.is_file() or sha256_file(dst_pdf) != sha256_file(pdf):
                probs.append(f"pdf mismatch {pdf.name}")
        total_files += checked
        problems += [f"{row['id']}: {p}" for p in probs]
        content_rel = index_content_file(dst_dir, row["id"])
        # FM-2: registered paths must resolve inside the V3 root, never a link out.
        for rel in filter(None, [f"{mdir.name}/{content_rel}" if content_rel else row["md_file_path"]]):
            real = (dst / rel).resolve()
            if dst not in real.parents or (dst / rel).is_symlink():
                problems.append(f"{row['id']}: path {rel} escapes V3 root")
        done.append((row, mdir.name, content_rel))
        print(f"verified {row['id']}: {checked} files, index_track={'yes' if content_rel else 'no'}")

    if problems:
        print("PROBLEMS:")
        for p in problems:
            print("  " + p)
        return 1
    print(f"all {total_files} files verified identical")
    if not args.verify:
        if not args.database_url:
            print("no --database-url: files copied, rows NOT registered")
            return 1
        inserted, skipped = register(done, args.database_url)
        print(f"registered: inserted={inserted} skipped_existing={skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
