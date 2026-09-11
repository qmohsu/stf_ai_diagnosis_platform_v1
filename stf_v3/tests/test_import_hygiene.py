"""Guards dev-plan D3: stf_v3 must never import V1/V2 code.

Author: Xiangzhu Yan
"""

import pathlib
import re

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "stf_v3"
_FORBIDDEN = re.compile(
    r"^\s*(?:from|import)\s+(diagnostic_api|obd_agent|app)(?:[\s.]|$)",
    re.MULTILINE,
)


def test_no_imports_from_legacy_packages() -> None:
    """Every module under src/stf_v3 is free of diagnostic_api/obd_agent/app
    imports, so the package can be moved to another repository unchanged."""
    offenders = []
    for path in _SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for match in _FORBIDDEN.finditer(text):
            offenders.append(f"{path.relative_to(_SRC)}: {match.group(0).strip()}")
    assert not offenders, "forbidden imports:\n" + "\n".join(offenders)


def test_metadata_registers_all_expected_tables() -> None:
    """Importing stf_v3.metadata registers exactly the Stage 1 tables."""
    from stf_v3.metadata import EXPECTED_TABLES, metadata

    assert set(metadata.tables) == set(EXPECTED_TABLES)
