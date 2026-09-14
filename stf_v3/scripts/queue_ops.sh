#!/usr/bin/env bash
# Queue operations for V3 (PROD-06 ops drills, dev plan §2.4 运维演练).
#
#   bash stf_v3/scripts/queue_ops.sh status            # backlog / stuck jobs per queue
#   bash stf_v3/scripts/queue_ops.sh failed            # dead letters with last error
#   bash stf_v3/scripts/queue_ops.sh retry <job_id>    # re-queue one failed job
#   bash stf_v3/scripts/queue_ops.sh cancel <job_id>   # cancel queued / abort running
#   bash stf_v3/scripts/queue_ops.sh drill             # restart-recovery drill (default queue)
#
# Runs on the PolyU server host.  SQL goes through the Postgres container
# with the V3 owner role URL from infra/.env (never printed).
set -euo pipefail

CMD="${1:-status}"
REPO_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
set -a; . "$REPO_DIR/infra/.env"; set +a
PG_CONTAINER="${PG_CONTAINER:-stf-postgres}"
DB_URL="${STF_V3_DATABASE_URL:?set STF_V3_DATABASE_URL}"
# strip the SQLAlchemy driver suffix for psql
PSQL_URL="${DB_URL/postgresql+psycopg:/postgresql:}"

sql() { podman exec -i "$PG_CONTAINER" psql "$PSQL_URL" -X -q "$@"; }

case "$CMD" in
  status)
    echo "== jobs per queue / status =="
    sql -c "SELECT queue_name, status, count(*) FROM procrastinate_jobs GROUP BY 1,2 ORDER BY 1,2"
    echo "== in flight or waiting (oldest first) =="
    sql -c "SELECT id, queue_name, task_name, status, attempts, scheduled_at,
                   now() - scheduled_at AS waiting
              FROM procrastinate_jobs WHERE status IN ('doing','todo')
              ORDER BY scheduled_at LIMIT 20"
    echo "== stuck > 30 min in 'doing' (candidates for stalled recovery) =="
    sql -c "SELECT j.id, j.task_name, j.attempts, max(e.at) AS last_event
              FROM procrastinate_jobs j JOIN procrastinate_events e ON e.job_id = j.id
             WHERE j.status = 'doing' GROUP BY j.id
            HAVING max(e.at) < now() - interval '30 minutes'" ;;
  failed)
    echo "== dead letters (status=failed) with their last event =="
    sql -c "SELECT j.id, j.task_name, j.attempts, j.args, e.type, e.at
              FROM procrastinate_jobs j
              JOIN LATERAL (SELECT type, at FROM procrastinate_events
                             WHERE job_id = j.id ORDER BY at DESC LIMIT 1) e ON true
             WHERE j.status = 'failed' ORDER BY e.at DESC LIMIT 20"
    echo "== manuals marked failed (reason from the pipeline) =="
    sql -c "SELECT id, filename, job_id, left(error_message, 120) AS reason, updated_at
              FROM manuals WHERE status = 'failed' ORDER BY updated_at DESC" ;;
  retry)
    JOB="${2:?job id}"
    podman exec -e STF_V3_DATABASE_URL="$DB_URL" stf-v3-api \
      procrastinate --app stf_v3.jobs.app.app retry "$JOB"
    sql -c "SELECT id, status, attempts FROM procrastinate_jobs WHERE id = $JOB" ;;
  cancel)
    JOB="${2:?job id}"
    podman exec -e STF_V3_DATABASE_URL="$DB_URL" stf-v3-api \
      python -c "import asyncio; from stf_v3.jobs.app import app
async def m():
    async with app.open_async():
        print(await app.job_manager.cancel_job_by_id_async($JOB, abort=True))
asyncio.run(m())" ;;
  drill)
    # Restart-recovery drill: defer a 60 s job on the default queue, restart
    # the container worker mid-flight, prove the job is picked up again and
    # finishes (procrastinate marks abandoned 'doing' jobs stalled → todo).
    echo "== deferring a 60 s drill job =="
    JOB=$(podman exec -e STF_V3_DATABASE_URL="$DB_URL" stf-v3-api \
      python -c "import asyncio; from stf_v3.jobs.app import app; from stf_v3.jobs import drill
async def m():
    async with app.open_async():
        print(await drill.sleep_task.defer_async(seconds=60))
asyncio.run(m())")
    echo "job $JOB"; sleep 8
    sql -c "SELECT id, status, attempts FROM procrastinate_jobs WHERE id = $JOB"
    echo "== restarting stf-v3-worker while the job runs =="
    podman restart stf-v3-worker >/dev/null; sleep 5
    sql -c "SELECT id, status, attempts FROM procrastinate_jobs WHERE id = $JOB"
    echo "== waiting up to 6 min for completion (recover_stalled runs every 2 min, stall threshold 120 s) =="
    for _ in $(seq 1 36); do
      st=$(sql -tA -c "SELECT status FROM procrastinate_jobs WHERE id = $JOB")
      [ "$st" = "succeeded" ] && break; sleep 10
    done
    sql -c "SELECT id, status, attempts FROM procrastinate_jobs WHERE id = $JOB"
    sql -c "SELECT type, at FROM procrastinate_events WHERE job_id = $JOB ORDER BY at"
    [ "$st" = "succeeded" ] && echo "DRILL PASS: job re-picked after restart (attempts above)" \
                             || { echo "DRILL FAIL: status=$st"; exit 1; } ;;
  *) echo "usage: $0 status|failed|retry <id>|cancel <id>|drill"; exit 2 ;;
esac
