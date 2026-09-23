# Golden eval data (PROD-10)

| File | What | Source (V2) |
|---|---|---|
| `golden/manual_mws150a.jsonl` | 30 manual goldens (MWS-150-A, index-track anchors) | `diagnostic_api/tests/harness/evals/golden/v2/locked/mws150a_indexed.jsonl` |
| `golden/obd_yamaha_road_test.jsonl` | 15 OBD goldens (Yamaha road test) | `…/golden/v2/locked/yamaha_road_test.jsonl` |
| `fixtures/yamaha_road_test.csv` | the road-test log the OBD goldens ask about | `obd_agent/fixtures/yamaha_dual_road_test_20260508.csv` |
| `MANIFEST.json` | sha256 of each file + V2 source hash + copy date | — |
| `thresholds.yaml` | baseline, gate rules, managed paths (written by `python -m stf_v3.evals accept`) | — |

Byte-for-byte copies of the committed V2 files; every run verifies the
manifest and refuses on any difference.  **Never edit in place** — a
revised set is a new file plus a baseline-reset PR (runbook §5.6).
`.gitattributes` marks these files `-text` so no checkout converts line
endings.
