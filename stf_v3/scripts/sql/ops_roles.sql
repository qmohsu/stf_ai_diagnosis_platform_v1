-- Operational database roles (PROD-15A).  Idempotent: applied as the cluster
-- superuser to the V3 database (the table grant below is per database) through
-- `bash stf_v3/scripts/db_roles.sh` (unix socket inside the database container,
-- never a password on a command line; no password ever appears in this file).
--
-- stf_v3_backup (FM-16): reads every table of every database through the
-- built-in pg_read_all_data role, starts every transaction read-only, and
-- has NO password.  Since 2026-09-27 local TCP needs a password, so this role
-- is usable only via `podman exec` into the database container.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stf_v3_backup') THEN
    CREATE ROLE stf_v3_backup LOGIN;
  END IF;
END
$$;
ALTER ROLE stf_v3_backup LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION
  PASSWORD NULL CONNECTION LIMIT 4;
ALTER ROLE stf_v3_backup SET default_transaction_read_only = on;
GRANT pg_read_all_data TO stf_v3_backup;

-- stf_v3_app (PROD-11 FM-43 / PROD-15A FM-45): the runtime role may hold at
-- most 50 connections, so a V3 leak or load spike cannot take the shared
-- instance (max_connections 100) away from V1/V2.  Sizing = every pool's
-- maximum (stf_v3/src/stf_v3/db.py): API 14 + container worker 14 + host GPU
-- worker 15 = 43, plus 7 for ops scripts run inside a container.  Undo:
-- ALTER ROLE stf_v3_app CONNECTION LIMIT -1;
ALTER ROLE stf_v3_app CONNECTION LIMIT 50;

-- stf_v3_eval (PROD-10 FM-16 / PROD-15A FM-12, FM-15): the golden eval reads
-- ONLY the manual catalog (evals/cli.py -> load_manual_inventory), starts every
-- transaction read-only and holds at most 10 connections.  Its password is
-- set by db_roles.sh from STF_V3_EVAL_DB_PASSWORD in infra/.env.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stf_v3_eval') THEN
    CREATE ROLE stf_v3_eval LOGIN;
  END IF;
END
$$;
ALTER ROLE stf_v3_eval LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION
  CONNECTION LIMIT 10;
ALTER ROLE stf_v3_eval SET default_transaction_read_only = on;
GRANT USAGE ON SCHEMA public TO stf_v3_eval;
GRANT SELECT ON TABLE manuals TO stf_v3_eval;
