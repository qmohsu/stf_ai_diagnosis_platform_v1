# Weekly Report — Manual-Agent Benchmark: v2.1 to v2.2

**Author:** Xiangzhu Yan
**Date:** 2026-07-21
**Scope:** This week closed the OBD golden expert-review cycle and
converted the manual lane's remaining stable failures into
root-cause fixes — DTC-procedure navigation, corrupted section
structure, and vehicle-identity delivery — each verified with a
measured before/after on the live server, merged, and deployed to
production.

---

## 1. Headline scores

| Milestone | Mean overall (30 locked goldens) | Notes |
|---|---:|---|
| v2.1 (2026-07-13) | 0.777 | Median 0.811, σ 0.195. |
| **v2.2 (2026-07-21)** | **0.831** | Median 0.887, **σ 0.136**, **30/30 above the pass floor — first all-green full run**. Mean wall 72 s. |

**Same-rubric weekly delta: 0.777 → 0.831 (+0.054).** Spread
tightened by a third (σ 0.195 → 0.136) — the gain came from fixing
the worst entries, not from inflating the top.

**OBD lane milestone (same week):** the expert-review cycle closed —
2 goldens revised per Deng Xiao's comments, the post-revision re-run
held **15/15 pass, mean 0.938**, and all 15 entries were promoted
into the locked tier. Both eval lanes now run entirely on
expert-approved locked goldens.

### Per question type (v2.1 → v2.2)

| Type | v2.1 | v2.2 |
|---|---:|---:|
| lookup | 0.839 | 0.841 |
| procedural | 0.741 | **0.945** |
| cross-section | 0.752 | 0.792 |
| image-required | 0.779 | 0.815 |
| adversarial | 0.774 | 0.759 |

Procedural — the weakest category two weeks ago (0.567 at v2) — is
now the strongest, on the strength of the DTC-navigation fixes below.

---

## 2. Problems found → fixes shipped → measured effect

| Problem (short) | Issue / PR | Measured effect |
|---|---|---|
| OBD goldens carried 2 unresolved expert-review comments | #205 / PR #206, #207, #208 | Both entries revised per review; re-run **15/15 pass holds (mean 0.938)**; all 15 promoted to the locked tier — OBD review cycle CLOSED |
| Judge could not see figures the agent surfaced (held last week) | #193 / PR #197 | Merged after confound resolution; image-required 0.686 → 0.785 in the same-rubric re-run |
| DTC Quick Index mapped codes to nothing (occurrence counts only) | #210 / PR #211 | Index now carries a per-code **section slug** column; dtc-001 0.400 → **0.988** |
| P-code diagnostic sections invisible & sliced by junk `### 註` headings | #210 / PR #211 | Banner filter extended; P0107 section recovered 112 → 15,264 chars; procedural-002 0.363 → **0.985** |
| Vehicle identity reached the manual sub-agent only via LLM free text | #213 / PR #214 (closes #204) | Deterministic `## VEHICLE` block injected from the session row (APP-60 fields + VIN); cross-004 0.347 → **0.755**, zero wrong-manual reads across all verification runs |
| Prod deployment | both PRs live | Single-container redeploys ×2, warm LLM preserved (no cold-load window) |

Supporting artifacts: every PR carries a branch-deploy verification
report; raw run data committed under `docs/eval-reports/`
(`harness24_manual_rerun_20260721.json`,
`harness28_manual_baseline_20260721.json`,
`harness29_vehicle_targeted.json`, `harness29_v2_2_baseline.json`,
`harness27_obd_rerun.json`).

---

## 3. Honest caveats on v2.2

- **The 30/30 all-green is against the 0.4 regression floor**; the
  working quality bar of 0.7 stands at 24/30. Both numbers are
  week-over-week highs (v2.1: 27/30 and 21/30 respectively).
- **Run variance is reduced but not gone** (σ 0.136): cross-003
  swung 0.802 → 0.425 → 0.745 across three same-day runs with no
  related change. Milestone means are trustworthy; single entries
  still are not.
- **Four stable low performers are now clearly isolated** (they
  repeat their scores across runs, unlike the noise cases):
  lookup-005 (0.578, identical across 4 runs), image-006 (0.612 ×3),
  cross-006 (0.483–0.612), adversarial-006 (0.646–0.660). These are
  next week's root-cause targets.
- cross-004's residual gap (0.755 vs golden 0.95+) is the
  spec-lookup half of the question — manual selection is fixed, the
  cross-section spec retrieval is not.

## 4. Next steps

1. Root-cause the two zero-variance stable lows: lookup-005 and
   image-006 — their run-to-run score stability suggests structural
   causes (like the DTC-navigation class fixed this week), not
   model noise.
2. Characterise cross-006 and adversarial-006 (borderline
   stable-low vs variance).
3. cross-004 spec-lookup completion (second read into the
   engine-specs table).
4. Umbrella #116 continues: next milestone eval after the above
   lands becomes v2.3.
