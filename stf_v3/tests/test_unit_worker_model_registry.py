"""PROD-11 server finding: a worker process must know EVERY table.

The first real diagnosis on the server ran its engine, then the final
transaction failed with ``NoReferencedTableError`` (``users``): a queue
worker imports only its task modules, which never import the auth /
workshop models, so a multi-table flush could not sort the tables.  The
error path and the sweeper hit the same error, leaving the conversation
"running" for hours.  The test suite missed it because every test process
imports the whole app first.  This test runs a FRESH interpreter that
imports exactly what a worker imports and resolves every foreign key.

Author: Xiangzhu Yan
"""

from __future__ import annotations

import os
import subprocess
import sys

_CODE = """
import stf_v3.jobs.app as a
for name in a.app.import_paths:
    __import__(name)
import stf_v3.diagnosis.job, stf_v3.diagnosis.store     # task bodies import these at run time
from stf_v3.db import Base
bad = []
for t in Base.metadata.tables.values():
    for fk in t.foreign_keys:
        try:
            fk.column
        except Exception as exc:
            bad.append((t.name, fk.target_fullname, type(exc).__name__))
present = set(Base.metadata.tables)
from stf_v3.metadata import EXPECTED_TABLES   # imported LAST: it registers every model itself
missing = sorted(set(EXPECTED_TABLES) - present)
print("OK" if not bad and not missing else f"BAD {bad} missing={missing}")
"""


def test_a_fresh_worker_process_resolves_every_foreign_key() -> None:
    """Importing only the worker's task modules registers every table, so a
    final transaction can always sort its inserts / updates."""
    env = dict(os.environ, PYTHONUTF8="1")
    out = subprocess.run([sys.executable, "-c", _CODE], capture_output=True, text=True, env=env, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().splitlines()[-1] == "OK", out.stdout
