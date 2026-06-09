# OBD Agent Setup Guide

`obd_agent/` is the OBD-II **log analysis library** and **end-of-trip
upload client** for the STF AI Diagnosis Platform.

> **History (APP-53, GitHub issue #76).** The package originally shipped
> a live ELM327 acquisition loop (`python -m obd_agent`) that POSTed
> per-snapshot JSON to `/v1/telemetry/obd_snapshot` — an endpoint that
> was never deployed. That loop, its readers, simulation scenarios,
> container mode, and the GPL-licensed `python-obd` dependency were
> removed on 2026-06-09. The supported ingestion path is the 4G
> end-of-trip upload below.

## What the package provides

- **Analysis pipeline** (imported by `diagnostic_api`):
  `format_normalizer` (OBDWIZ / obd_maxlog / Yamaha dual-channel CSV →
  canonical TSV), `time_series_normalizer`, `statistics_extractor`,
  `anomaly_detector`, `clue_generator`, `log_parser`, `log_summarizer`,
  `summary_formatter`.
- **Upload client**: `jetson_uploader` — logs in, uploads a trip log
  file to `POST /v2/obd/analyze`, prints the resulting `session_id`.

## Uploading a trip log (Jetson / any edge device)

```bash
pip install ./obd_agent          # or: pip install -r obd_agent/requirements.txt

python -m obd_agent.jetson_uploader \
    --base-url https://stf-diagnosis.dev \
    --username <user> \
    --password <password> \
    --log-file /var/log/obd/trip_20260505_164119.csv
```

- Exit code `0` and a `session_id` on stdout indicate success.
- Accepted formats: native TSV, OBDWIZ CSVLog, obd_maxlog CSV,
  Yamaha Dual OBDLink EX CSV (`A_KL_*` / `A_YAM_*` columns), generic
  CSV. Max upload size 10 MB.
- Re-login happens per upload (no token caching) — cheap by design.

Programmatic use: `from obd_agent.jetson_uploader import login,
upload_log, upload_trip`.

## Running tests

```bash
cd obd_agent
pip install -r requirements.txt   # GPL-free
pytest tests/ -v
```

Or from `infra/`: `make obd-agent-test`.

## Troubleshooting

### Upload fails with HTTP 401
Check the username/password against the deployment you target; accounts
are registered via the web UI (`/register`) or `POST /auth/register`.

### Upload fails with HTTP 413 or "file too large"
`POST /v2/obd/analyze` caps uploads at 10 MB. Split very long trips
into multiple files.

### "0 recognised PIDs" in the resulting session
The format auto-detection did not recognise the column layout. Check
that the header row matches one of the supported formats above; for
new logger formats, extend `obd_agent/format_normalizer.py`.
