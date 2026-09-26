"""PROD-11 operator scripts: the pre-deploy guard (FM-34, T-23) and the
"what can this account see" check for the test workshop (FM-7, T-25).

Author: Xiangzhu Yan
"""

from __future__ import annotations

import pathlib
import sys

from tests.conftest import register_and_login, requires_db
from tests.diagnosis_helpers import FAKE_VIN_B, second_workshop, seed

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"


def test_predeploy_guard_refuses_by_default() -> None:
    """FM-34: unfinished diagnoses or a converting manual → exit 3 unless
    ALLOW_INTERRUPT=1; the owner URL is never echoed."""
    text = (SCRIPTS / "predeploy_check.sh").read_text(encoding="utf-8")
    assert "status IN ('queued','running')" in text and "status = 'converting'" in text
    assert 'ALLOW_INTERRUPT:-0}" = "1"' in text and "exit 3" in text
    assert "echo \"$PSQL_URL" not in text and "echo $DB_URL" not in text


@requires_db
async def test_visible_vehicles_lists_only_the_accounts_workshop(client, workshop_with_codes, capsys) -> None:  # type: ignore[no-untyped-def]
    """FM-7: a member of the test workshop sees only that workshop's vehicles;
    VINs are masked to their last four characters."""
    sys.path.insert(0, str(SCRIPTS))
    import visible_vehicles

    wid, codes = workshop_with_codes
    await seed(client, wid, codes, user="real_tech")                   # the "real" fleet
    wid_b, codes_b = await second_workshop("Test Workshop (frontend)")
    await seed(client, wid_b, codes_b, user="student1", vin=FAKE_VIN_B)
    assert await visible_vehicles._run("student1") == 0
    out = capsys.readouterr().out
    assert "1 workshop(s)" in out and "Test Workshop (frontend)" in out
    assert "vin *************3456" in out and FAKE_VIN_B not in out
    assert "'Test Workshop'" not in out                                   # the real fleet is not listed
    assert await visible_vehicles._run("nobody") == 2
