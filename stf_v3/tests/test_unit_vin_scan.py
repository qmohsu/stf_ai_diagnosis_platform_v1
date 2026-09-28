"""PROD-15A T-27 / FM-52: no real VIN in the (public) repository.

Author: Xiangzhu Yan
"""

import importlib.util
import pathlib
import sys
from typing import Any

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "check_no_vins.py"
_spec = importlib.util.spec_from_file_location("check_no_vins", _SCRIPT)
assert _spec and _spec.loader
cv: Any = importlib.util.module_from_spec(_spec)
sys.modules["check_no_vins"] = cv
_spec.loader.exec_module(cv)

# Built at run time so this file itself holds no VIN-shaped literal.
NOT_ALLOWED = "WDB" + "906633" + "15A" + "12345"
assert len(NOT_ALLOWED) == 17


def test_the_two_project_fakes_pass_and_any_other_vin_fails() -> None:
    """Allow-listed fakes pass; another 17-char VIN shape fails, reported masked."""
    text = "ok JHMGK5830HX202404 and 1HGCM82633A123456\nbad " + NOT_ALLOWED + " here\n"
    found = cv.scan_text(text)
    assert found == [(2, cv.mask(NOT_ALLOWED))]
    assert NOT_ALLOWED not in found[0][1] and found[0][1].startswith("WDB…#")


def test_numbers_words_and_longer_tokens_are_not_vins() -> None:
    """Timestamps / ids (digits only), words, hashes and 18+ chars are ignored."""
    text = "20260927143025123 ABCDEFGHJKLMNPRST 3f1c1b0e3f1c1b0e3f1c " + NOT_ALLOWED + "9 x" + NOT_ALLOWED
    assert cv.scan_text(text) == []


def test_binary_files_are_skipped(tmp_path: pathlib.Path) -> None:
    """A NUL in the first bytes = binary (images, dumps)."""
    (tmp_path / "a.bin").write_bytes(b"\0" + NOT_ALLOWED.encode())
    (tmp_path / "b.md").write_text("vin " + NOT_ALLOWED)
    out = cv.scan_files([tmp_path / "a.bin", tmp_path / "b.md"], tmp_path)
    assert out == [f"b.md:1: VIN-shaped {cv.mask(NOT_ALLOWED)}"]


def test_the_repository_holds_no_real_vin() -> None:
    """Every git-tracked file of this checkout passes (also a CI job of its own,
    ``vin-scan``; skipped where the package was copied out of Git, e.g. the
    portability check)."""
    root = _SCRIPT.parents[2]
    if not (root / ".git").exists():
        pytest.skip("not a git checkout")
    assert cv.scan_files(cv.tracked_files(root), root) == []
