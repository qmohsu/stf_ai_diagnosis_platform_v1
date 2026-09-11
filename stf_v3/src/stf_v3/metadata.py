"""Single import point that registers every model on ``Base.metadata``.

Alembic's ``env.py`` and the schema check script import ``metadata`` from
here so that no table is forgotten when a new module adds models.

Author: Xiangzhu Yan
"""

from sqlalchemy import MetaData

from stf_v3.auth import models as _auth  # noqa: F401
from stf_v3.db import Base
from stf_v3.diagnosis import models as _diagnosis  # noqa: F401
from stf_v3.ingest import models as _ingest  # noqa: F401
from stf_v3.knowledge import models as _knowledge  # noqa: F401
from stf_v3.vehicles import models as _vehicles  # noqa: F401
from stf_v3.workshops import models as _workshops  # noqa: F401

metadata: MetaData = Base.metadata

# Tables owned by procrastinate (created from its bundled schema.sql in the
# initial migration).  Excluded from autogenerate / ``alembic check``.
PROCRASTINATE_PREFIX = "procrastinate_"

EXPECTED_TABLES = frozenset(
    {
        "users",
        "invite_codes",
        "workshops",
        "memberships",
        "vehicles",
        "vehicle_devices",
        "obd_logs",
        "diagnosis_conversations",
        "messages",
        "reports",
        "audit_events",
        "manuals",
    }
)
