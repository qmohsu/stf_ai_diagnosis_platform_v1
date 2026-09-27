-- Operational database roles (PROD-15A).  Idempotent: applied as the cluster
-- superuser through `bash stf_v3/scripts/db_roles.sh` (unix socket inside the
-- database container, never a password on a command line).
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
