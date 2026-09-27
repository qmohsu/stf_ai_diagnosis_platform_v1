# Sourced by the V3 ops scripts.  Turns a SQLAlchemy URL (in $DB_URL) into
# libpq variables so psql never gets the password on its command line
# (PROD-15A: `ps` on the shared server shows every argument).  The URL
# reaches python through the environment, not argv.  Use as:
#   podman exec -e PGPASSWORD -i "$PG_CONTAINER" psql -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" …
eval "$(DB_URL="$DB_URL" python3 -c '
import os, shlex
from urllib.parse import urlsplit, unquote
u = urlsplit(os.environ["DB_URL"].replace("postgresql+psycopg:", "postgresql:"))
for k, v in (("PGUSER", unquote(u.username or "")), ("PGPASSWORD", unquote(u.password or "")),
             ("PGHOST", u.hostname or "127.0.0.1"), ("PGPORT", str(u.port or 5432)),
             ("PGDATABASE", u.path.lstrip("/"))):
    print(f"{k}={shlex.quote(v)}")
')"
export PGPASSWORD
