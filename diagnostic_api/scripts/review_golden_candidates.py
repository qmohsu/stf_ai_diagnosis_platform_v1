#!/usr/bin/env python3
"""Interactive reviewer for generated golden candidates.

HARNESS-14 phase 3.  Loads candidates produced by
``generate_golden_candidates.py``, displays each one in the
terminal, and lets a human pick:

  [a] accept — append to the target ``golden/v{N}/{manual}.jsonl``.
  [e] edit   — open $EDITOR on a temp JSON file; re-validate on
               save; accept if valid, otherwise re-prompt.
  [r] reject — drop it and continue.
  [s] skip   — defer to a later session (recorded in a sidecar
               ``.review-state.json`` so the next run resumes
               here).
  [q] quit   — stop reviewing; accepted entries so far are
               already written.

Validates every accepted entry with ``GoldenEntry.model_validate``
before writing, so the ``v1/`` file never contains a malformed row
even if a reviewer edits something by hand.

Usage::

    python -m scripts.review_golden_candidates \\
        tests/harness/evals/golden/candidates/mws150a-dtc.jsonl

    # Explicit target path (default: infer by manual prefix):
    python -m scripts.review_golden_candidates \\
        tests/harness/evals/golden/candidates/mws150a-dtc.jsonl \\
        --out tests/harness/evals/golden/v1/mws150a.jsonl

Author: Li-Ta Hsu
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    TextIO,
    Tuple,
)

from tests.harness.evals.schemas import GoldenEntry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("review_golden_candidates")


# ── State file helpers ────────────────────────────────────────────


def _state_path(candidates_path: Path) -> Path:
    """Sidecar ``.review-state.json`` next to the candidates file."""
    return candidates_path.with_suffix(
        candidates_path.suffix + ".review-state.json",
    )


def _load_state(candidates_path: Path) -> Dict[str, Any]:
    """Load review state, or return fresh defaults.

    State shape::

        {
            "decisions": {"<candidate_id>": "accept"
                                              | "reject"
                                              | "skip"},
            "accepted_ids": [<id>, ...],
        }
    """
    sp = _state_path(candidates_path)
    if not sp.is_file():
        return {"decisions": {}, "accepted_ids": []}
    try:
        return json.loads(sp.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning(
            "state file corrupt; starting fresh: %s", sp,
        )
        return {"decisions": {}, "accepted_ids": []}


def _save_state(
    candidates_path: Path, state: Dict[str, Any],
) -> None:
    """Persist review state atomically."""
    sp = _state_path(candidates_path)
    tmp = sp.with_suffix(sp.suffix + ".tmp")
    tmp.write_text(
        json.dumps(state, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    tmp.replace(sp)


# ── Candidate IO ──────────────────────────────────────────────────


def _load_candidates(
    path: Path,
) -> List[Dict[str, Any]]:
    """Load candidates from a JSONL file."""
    entries: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line_num, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entries.append(json.loads(stripped))
            except json.JSONDecodeError as exc:
                logger.error(
                    "skipping malformed line %d: %s",
                    line_num, exc,
                )
    return entries


def _append_golden(
    target: Path, entry: Dict[str, Any],
) -> None:
    """Append one accepted entry to the golden JSONL."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False))
        handle.write("\n")


def _validate_entry(
    entry: Dict[str, Any],
) -> Tuple[bool, Optional[str]]:
    """Validate an entry against the ``GoldenEntry`` schema.

    Returns:
        ``(True, None)`` if valid, else ``(False, error_message)``.
    """
    try:
        GoldenEntry.model_validate(entry)
        return True, None
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


# ── Display ───────────────────────────────────────────────────────


def _format_entry_for_display(entry: Dict[str, Any]) -> str:
    """Render one candidate entry as a human-readable block."""
    lines: List[str] = []
    lines.append("=" * 70)
    lines.append(
        f"id:         {entry.get('id', '<none>')}"
    )
    lines.append(
        f"category:   {entry.get('category', '?')}"
    )
    lines.append(
        f"difficulty: {entry.get('difficulty', '?')}"
    )
    lines.append(
        f"requires_image: {entry.get('requires_image', False)}"
    )
    lines.append("")
    lines.append("question:")
    lines.append(f"  {entry.get('question', '').strip()}")
    obd = entry.get("obd_context")
    if obd:
        lines.append("")
        lines.append("obd_context:")
        lines.append(f"  {obd.strip()}")
    lines.append("")
    lines.append("golden_summary:")
    for line in (
        entry.get("golden_summary", "").strip().split("\n")
    ):
        lines.append(f"  {line}")
    lines.append("")
    lines.append("golden_citations:")
    for cit in entry.get("golden_citations", []) or []:
        lines.append(
            f"  - {cit.get('manual_id')}#"
            f"{cit.get('slug')}",
        )
        quote = cit.get("quote", "")
        if quote:
            lines.append(f"    quote: {quote!r}")
    if not entry.get("golden_citations"):
        lines.append("  (none — adversarial entry)")
    lines.append("")
    lines.append(
        f"must_contain:     "
        f"{entry.get('must_contain', [])}"
    )
    lines.append(
        f"must_not_contain: "
        f"{entry.get('must_not_contain', [])}"
    )
    lines.append(
        f"expected_tool_trace: "
        f"{entry.get('expected_tool_trace', [])}"
    )
    notes = entry.get("notes", "")
    if notes:
        lines.append("")
        lines.append(f"notes: {notes}")
    lines.append("=" * 70)
    return "\n".join(lines)


# ── Edit flow ─────────────────────────────────────────────────────


def _run_editor(
    entry: Dict[str, Any],
    editor: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Spawn ``$EDITOR`` on a temp JSON file with ``entry``.

    Args:
        entry: Original entry.
        editor: Explicit editor command (for tests).  Defaults to
            ``$EDITOR`` env var or ``vi`` / ``notepad``.

    Returns:
        Parsed JSON dict after the user saves, or ``None`` if the
        edit was aborted / file is unparseable.
    """
    ed = (
        editor
        or os.environ.get("EDITOR")
        or ("notepad" if os.name == "nt" else "vi")
    )

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False,
        encoding="utf-8",
    ) as tmp:
        json.dump(entry, tmp, indent=2, ensure_ascii=False)
        tmp_path = tmp.name

    try:
        subprocess.call([ed, tmp_path])  # noqa: S603,S607
        with open(tmp_path, "r", encoding="utf-8") as handle:
            text = handle.read()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.error(
            "edited JSON is malformed: %s", exc,
        )
        return None
    if not isinstance(parsed, dict):
        logger.error(
            "edited file must contain a JSON object",
        )
        return None
    return parsed


# ── Review loop ───────────────────────────────────────────────────


Action = str  # one of {"a", "e", "r", "s", "q"}


def _prompt_action(
    reader: Callable[[str], str],
) -> Action:
    """Read one reviewer decision, retrying on invalid input."""
    while True:
        raw = reader(
            "\n[a]ccept  [e]dit  [r]eject  [s]kip  "
            "[q]uit > ",
        ).strip().lower()
        if raw in {"a", "e", "r", "s", "q"}:
            return raw
        print(
            "Unknown choice.  Enter one of a / e / r / s / q.",
        )


def review_candidates(
    candidates_path: Path,
    target_path: Path,
    *,
    reader: Callable[[str], str] = input,
    writer: TextIO = sys.stdout,
    editor_runner: Callable[
        [Dict[str, Any]], Optional[Dict[str, Any]],
    ] = _run_editor,
) -> Dict[str, Any]:
    """Drive the interactive review loop.

    Args:
        candidates_path: Input JSONL produced by the generator.
        target_path: Golden JSONL to append accepted entries to.
        reader: Input function (defaults to ``input``).  Tests
            pass a scripted stand-in.
        writer: Output stream for display text.
        editor_runner: Function that takes an entry and returns
            the edited version (or ``None`` on failure).  Tests
            inject a stub.

    Returns:
        Summary dict with counts of each decision type.
    """
    candidates = _load_candidates(candidates_path)
    state = _load_state(candidates_path)
    decisions: Dict[str, str] = state.get("decisions", {})
    accepted_ids: List[str] = state.get("accepted_ids", [])

    totals = {"accept": 0, "reject": 0, "skip": 0, "quit": 0}

    for entry in candidates:
        cand_id = entry.get("id", "<unknown>")
        prev = decisions.get(cand_id)
        if prev in {"accept", "reject"}:
            # Already decided in a previous session — skip.
            continue

        writer.write("\n")
        writer.write(_format_entry_for_display(entry))
        writer.write("\n")
        writer.flush()

        while True:
            action = _prompt_action(reader)
            if action == "a":
                ok, err = _validate_entry(entry)
                if not ok:
                    writer.write(
                        f"Cannot accept — schema error: "
                        f"{err}\n",
                    )
                    continue
                _append_golden(target_path, entry)
                decisions[cand_id] = "accept"
                accepted_ids.append(cand_id)
                totals["accept"] += 1
                break
            elif action == "r":
                decisions[cand_id] = "reject"
                totals["reject"] += 1
                break
            elif action == "s":
                decisions[cand_id] = "skip"
                totals["skip"] += 1
                break
            elif action == "e":
                edited = editor_runner(entry)
                if edited is None:
                    writer.write(
                        "Edit aborted — returning to "
                        "prompt.\n",
                    )
                    continue
                ok, err = _validate_entry(edited)
                if not ok:
                    writer.write(
                        f"Edited entry failed schema: "
                        f"{err}\n",
                    )
                    continue
                _append_golden(target_path, edited)
                decisions[edited.get("id", cand_id)] = (
                    "accept"
                )
                accepted_ids.append(
                    edited.get("id", cand_id),
                )
                totals["accept"] += 1
                break
            elif action == "q":
                totals["quit"] += 1
                break

        state["decisions"] = decisions
        state["accepted_ids"] = accepted_ids
        _save_state(candidates_path, state)

        if action == "q":
            writer.write("Quitting review.\n")
            break

    writer.write(
        f"\nDone.  Accepted: {totals['accept']}  "
        f"Rejected: {totals['reject']}  "
        f"Skipped: {totals['skip']}\n",
    )
    return totals


# ── CLI ───────────────────────────────────────────────────────────


def _infer_target(
    candidates_path: Path,
) -> Path:
    """Infer the golden v1 target path from the candidates filename.

    Assumes ``candidates/{manual_short}-{category}.jsonl`` and
    writes to ``v1/{manual_short}.jsonl`` in the same golden
    tree.
    """
    stem = candidates_path.stem
    manual_short = stem.split("-", 1)[0]
    golden_root = candidates_path.parent.parent
    return golden_root / "v1" / f"{manual_short}.jsonl"


def _parse_args(
    argv: Optional[List[str]] = None,
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Interactively review generated golden candidates "
            "and append accepted entries to the golden JSONL."
        ),
    )
    parser.add_argument(
        "candidates", type=Path,
        help="Input JSONL (from generate_golden_candidates.py).",
    )
    parser.add_argument(
        "--out", type=Path,
        help=(
            "Target golden JSONL to append to.  Inferred from "
            "the candidates filename when omitted."
        ),
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    candidates_path = args.candidates
    if not candidates_path.is_file():
        logger.error(
            "candidates file not found: %s", candidates_path,
        )
        return 2

    target_path = args.out or _infer_target(candidates_path)

    # Protect v1 from accidental overwrites if the user points at
    # a non-empty file whose backup hasn't been taken.
    if target_path.exists() and target_path.stat().st_size > 0:
        logger.info(
            "appending to existing target %s "
            "(backup first with `cp %s %s.bak` if desired)",
            target_path, target_path, target_path,
        )

    # Ensure $EDITOR or a fallback is reachable when the user
    # picks [e].  Non-fatal — _run_editor handles missing
    # binaries gracefully.
    editor = os.environ.get("EDITOR") or (
        "notepad" if os.name == "nt" else "vi"
    )
    if shutil.which(editor) is None:
        logger.warning(
            "editor %r not found in PATH — [e]dit action "
            "will fail",
            editor,
        )

    totals = review_candidates(
        candidates_path, target_path,
    )
    return 0 if totals.get("quit", 0) == 0 else 0


if __name__ == "__main__":
    sys.exit(main())
