-- Run ONCE on a restored stf_v3 database, before any V3 process starts
-- (PROD-15A FM-9).  Work that was queued or running at backup time must not
-- run again: queued jobs are cancelled, running ones failed (procrastinate's
-- own functions, so its events stay consistent).  The diagnosis sweeper then
-- closes their conversations as "diagnosis_interrupted" on first start.  The
-- on-demand model controller forgets any restored "we started vLLM" state.
BEGIN;
SELECT count(procrastinate_cancel_job_v1(id, false, false))
  FROM procrastinate_jobs WHERE status = 'todo';
SELECT count(procrastinate_finish_job_v1(id, 'failed'::procrastinate_job_status, false))
  FROM procrastinate_jobs WHERE status = 'doing';
UPDATE model_service_state
   SET state = 'stopped', blocked_reason = NULL, started_by_us = false,
       requested_at = NULL, ready_at = NULL, failed_at = NULL, failure_reason = NULL,
       cooldown_until = NULL, gpu_snapshot = NULL, updated_at = now();
COMMIT;
