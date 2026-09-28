#!/usr/bin/env bash
# Applies the operational database roles (stf_v3/scripts/sql/ops_roles.sql)
# inside the database container as its superuser (unix socket, no password):
# stf_v3_backup (backup reader), stf_v3_app's connection limit, stf_v3_eval
# (golden eval, manual catalog only).  Idempotent; part of the restore
# procedure (PROD-15A FM-24).
#
#   bash stf_v3/scripts/db_roles.sh                       # roles + limits + eval password from infra/.env
#   bash stf_v3/scripts/db_roles.sh --init-eval-password  # first time: create the eval password + URL in infra/.env
#
# Secrets never reach a command line or the terminal: the eval password goes
# from infra/.env through the environment into psql's \getenv.
set -euo pipefail
PG_CONTAINER="${PG_CONTAINER:-stf-postgres}"
V3_DB="${V3_DB:-stf_v3}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${ENV_FILE:-$HERE/../../infra/.env}"

if [ "${1:-}" = "--init-eval-password" ]; then
  # Appends STF_V3_EVAL_DB_PASSWORD and STF_V3_EVAL_DATABASE_URL (same host /
  # port / database as the runtime URL) unless already present.  Prints names only.
  ENV_FILE="$ENV_FILE" python3 - <<'PY'
import os, secrets
from urllib.parse import urlsplit
path = os.environ["ENV_FILE"]
lines = open(path, encoding="utf-8").read().splitlines()
keys = {ln.split("=", 1)[0].strip() for ln in lines if "=" in ln and not ln.lstrip().startswith("#")}
if "STF_V3_EVAL_DATABASE_URL" in keys:
    print("eval password: already in infra/.env (unchanged)")
    raise SystemExit(0)
app = next(ln.split("=", 1)[1].strip() for ln in lines if ln.startswith("STF_V3_APP_DATABASE_URL="))
u = urlsplit(app)
pw = secrets.token_hex(24)
url = f"{u.scheme}://stf_v3_eval:{pw}@{u.hostname}:{u.port or 5432}{u.path}"
with open(path, "a", encoding="utf-8") as fh:
    fh.write(f"\n# PROD-15A: golden eval read-only role (db_roles.sh)\nSTF_V3_EVAL_DB_PASSWORD={pw}\nSTF_V3_EVAL_DATABASE_URL={url}\n")
os.chmod(path, 0o600)
print("eval password: written to infra/.env (STF_V3_EVAL_DB_PASSWORD, STF_V3_EVAL_DATABASE_URL)")
PY
fi

SU="$(podman exec "$PG_CONTAINER" printenv POSTGRES_USER)"
podman exec -i "$PG_CONTAINER" psql -U "$SU" -d "$V3_DB" -v ON_ERROR_STOP=1 -X -q \
  < "$HERE/sql/ops_roles.sql"

STF_V3_EVAL_DB_PASSWORD="$(ENV_FILE="$ENV_FILE" python3 -c '
import os
for ln in open(os.environ["ENV_FILE"], encoding="utf-8"):
    if ln.startswith("STF_V3_EVAL_DB_PASSWORD="):
        print(ln.split("=", 1)[1].strip())
        break
')"
export STF_V3_EVAL_DB_PASSWORD
if [ -n "$STF_V3_EVAL_DB_PASSWORD" ]; then
  printf '%s\n' '\getenv pw STF_V3_EVAL_DB_PASSWORD' "ALTER ROLE stf_v3_eval PASSWORD :'pw';" \
    | podman exec -i -e STF_V3_EVAL_DB_PASSWORD "$PG_CONTAINER" psql -U "$SU" -d postgres -v ON_ERROR_STOP=1 -X -q
else
  echo "WARN: no STF_V3_EVAL_DB_PASSWORD in infra/.env — stf_v3_eval cannot log in (run with --init-eval-password)"
fi

# Report shape only (never a password hash).
podman exec "$PG_CONTAINER" psql -U "$SU" -d postgres -X -A -t -c \
  "SELECT rolname || ' login=' || rolcanlogin || ' super=' || rolsuper || ' limit=' || rolconnlimit
          || ' password=' || (rolpassword IS NOT NULL)
     FROM pg_authid WHERE rolname LIKE 'stf_v3%' ORDER BY 1"
