# Weekly Report — 2026-07-14 → 2026-07-21 (all workstreams)

**Author:** Xiangzhu Yan
**Date:** 2026-07-21
**Scope:** Full work log since the v2.1 report (2026-07-13), across
both agent lanes and platform work: the OBD lane's expert-review
cycle was closed and its golden tier locked (15/15, mean 0.938);
the manual lane went v2.1 → v2.2 (0.777 → 0.831, first all-green
run) through three root-cause fixes; two production deployments
shipped; and the repo's report/eval archive was brought fully
up to date.

---

## 1. Executive summary

| Lane | Milestone this week |
|---|---|
| **OBD agent** | Expert-review cycle CLOSED: 2 goldens revised per reviewer comments, post-revision eval **15/15 pass holds (mean 0.938)**, all 15 promoted into the locked tier. The OBD eval now runs on expert-approved goldens by default. |
| **Manual agent** | **v2.2 = 0.831 mean, 30/30 above the pass floor** (first all-green full run; v2.1 was 0.777). Three root-cause fixes: DTC-index navigation, NOTE-banner section slicing, deterministic vehicle injection. |
| **Production** | Both manual-lane fixes deployed to the PolyU server (2 single-container redeploys, warm LLM preserved throughout — zero cold-load windows). |
| **Repo hygiene** | 5 feature PRs + 2 archive PRs merged; 4 issues closed (#204, #205, #210, #213); all report deliverables (06-27, 07-13, 07-21) and milestone eval artifacts now tracked in-repo; single-main branch state restored on GitHub / local / server. |

---

## 2. OBD agent lane — HARNESS-27: expert-review closure (07-19 → 07-20)

The 15 OBD goldens had been awaiting expert re-review since
2026-06-08, when reviewer Deng Xiao left 2 of 15 entries at
`needs_revision`. This week closed the loop end to end:

**a) Golden revisions per review comments (#205 / PR #206).**
- `compound-001` — reviewer: "idle RPM is not 0; state the basis of
  the no-fault claim." Fixture re-analysis confirmed RPM=0 only for
  the first 3 samples (engine off) and true idle at ~1475–1990 rpm
  (median ~1550). The summary now separates engine-off from idle,
  spells out the multi-signal basis for the no-misfire/no-overheat
  conclusion, and adds a caveat that 1 Hz sampling cannot exclude
  brief misfire.
- `dtc-decode-002` — reviewer: per the Yamaha manual (pp. 8-36/8-37)
  P0117 is "ECT sensor — short to ground detected", not just the
  generic SAE "circuit low voltage". The summary now gives both
  definitions with page references, and the pitfall accepts either
  phrasing.

**b) Post-revision eval re-run (#205 / PR #207).**
Full 15-golden run on the shipping `qwen3.5:27b-q8_0` (28m51s):
**15/15 pass holds, mean 0.938** (`harness27_obd_rerun.json`,
supersedes `harness25_obd_round1.json` as the OBD gate reference).
Both revised entries hold (compound-001 0.839 → 0.840,
dtc-decode-002 0.800 → 0.800). One live judge failure (the #201
"empty judge content" mode) hit compound-001 mid-run and was
resolved by a standalone re-run merged into the report with
provenance. Sole notable delta: adversarial-002 0.993 → 0.742
(phrasing variance, judge itself scored 0.95).

**c) Locked-tier promotion (#205 / PR #208).**
All 15 goldens promoted into `golden/v2/locked/yamaha_road_test.jsonl`
via `promote_golden.py` against the prod review DB: 13 through the
normal review-quality gate (5★ accepts), the 2 revised entries with
`--force` + out-of-band re-approval, all recorded in `PROMOTIONS.md`.
This closes the HARNESS-20 safety-net gap (the locked OBD file had
shipped empty) — **both eval lanes now run entirely on
expert-approved locked goldens by default.** Issue #205 closed.

---

## 3. Manual agent lane — v2.1 → v2.2 (07-19 → 07-21)

### Headline

| Milestone | Mean (30 locked goldens) | Notes |
|---|---:|---|
| v2.1 (2026-07-13) | 0.777 | Median 0.811, σ 0.195. |
| **v2.2 (2026-07-21)** | **0.831** | Median 0.887, **σ 0.136**, **30/30 above the pass floor — first all-green full run**. Mean wall 72 s. |

Spread tightened by a third — the gain came from fixing the worst
entries, not inflating the top. Per question type:

| Type | v2.1 | v2.2 |
|---|---:|---:|
| lookup | 0.839 | 0.841 |
| procedural | 0.741 | **0.945** |
| cross-section | 0.752 | 0.792 |
| image-required | 0.779 | 0.815 |
| adversarial | 0.774 | 0.759 |

Procedural — the weakest category at v2 (0.567) — is now the
strongest.

### Work items

| Work item | Issue / PR | Measured effect |
|---|---|---|
| Judge blind to figures the agent surfaced (held last week pending confound resolution) | #193 / PR #197 (merged 07-19) | image-required 0.686 → 0.785 in the same-rubric re-run |
| Re-measure the lane after the WP1/WP3 prompt fixes (numbers were 9 days stale) | re-run 07-21 | 0.705 → 0.776 (22/30); confirmed WP1/WP3 worked and isolated 2 structural failures |
| DTC Quick Index mapped codes to nothing (occurrence counts only); P-code sections depth-4 invisible with parent chain cut by junk NOTE banners | #210 / PR #211 (HARNESS-28) | dtc-001 0.400 → **0.988**; procedural-002 0.363 → **0.985**; P0107 section content recovered 112 → 15,264 chars |
| Vehicle identity reached the manual sub-agent only via LLM free text — vehicle-less inquiries made manual selection a coin flip (cross-004 answered a Yamaha question from the Corolla Haynes manual) | #213 / PR #214 (HARNESS-29, closes #204) | Deterministic `VEHICLE` block injected from the session row (APP-60 make/model + VIN); cross-004 0.347 → **0.755**, zero wrong-manual reads in all verification runs |
| v2.2 milestone baseline | PR #215 | **0.831, 30/30 all-green** (`harness29_v2_2_baseline.json`) |

Design decision of note (HARNESS-29): vehicle identity is injected
by **harness code, not LLM text** — extending the APP-59/APP-60
"identity is captured at upload" guarantee through the delegation
link. The eval mirrors the exact production interface
(`GoldenEntry.vehicle` + corpus default), so no golden files
changed and no locked-tier re-review was triggered.

---

## 4. Platform, deployment & repo hygiene

- **Two production deployments** (PolyU server) using the
  single-container recreate procedure — Postgres and the warm
  Ollama model stayed up through both; no cold-load window for
  users. Prod now runs full v2.2 code.
- **Boundary items merged 07-13 after the v2.1 report was issued:**
  APP-64 (PR #189) — V1 premium diagnose now passes the session's
  `vehicle_model` to retrieval (same vehicle-identity thread as
  HARNESS-29); HARNESS-25 OBD round-1 report (PR #190).
- **v2.1 fix-stack artifacts landed 07-14** (#196 coverage gate +
  manual pinning, #198 caption-stub demotion, #199 slug-tolerant
  claim_precision): the code was covered in last week's report; the
  53-min v2.1 milestone eval it produced
  (`phase6_v2_1_manual_lane.json`) had been left untracked and was
  committed retroactively this week (PR #209) with provenance.
- **Report archive completed:** 06-27 progress report and 07-13
  weekly report (md+pdf) — previously existing only on local disk —
  are now tracked under `docs/reports/`, alongside this report.
- **Branch state:** all feature branches deleted after merge on
  GitHub, local, and server; final state is a single `main`
  everywhere; 0 open PRs at week close.
- Eval-run artifacts committed this week under `docs/eval-reports/`:
  `harness27_obd_rerun.json`, `harness24_manual_rerun_20260721.json`,
  `harness28_manual_baseline_20260721.json` +
  `harness28_dtc_slug_targeted.json` + `harness28_dtc_slug_round2.json`,
  `harness29_vehicle_targeted.json`, `harness29_v2_2_baseline.json`,
  `phase6_v2_1_manual_lane.json`.

---

## 5. Honest caveats

- **Manual lane:** the 30/30 all-green is against the 0.4 regression
  floor; the 0.7 quality bar stands at 24/30 (both week-over-week
  highs). Run variance is reduced but real (cross-003 swung
  0.802 → 0.425 → 0.745 across three same-day runs). Four stable
  low performers are now clearly isolated: lookup-005 (0.578 ×4
  runs), image-006 (0.612 ×3), cross-006 (0.483–0.612),
  adversarial-006 (0.646–0.660).
- **OBD lane:** dtc-decode-002 sits exactly at the 0.7 threshold
  (0.800 overall is healthy; its weakest rubric line is the decode
  pivot) — the lane's most fragile entry. adversarial-002's
  0.993 → 0.742 swing is the OBD lane's variance analogue; same
  caution as the manual lane: trust means, not single entries.
- **cross-004's residual gap** (0.755 vs 0.95+) is the spec-lookup
  half of its two-part question — manual *selection* is fixed,
  cross-section spec retrieval is not.

## 6. Next steps

1. Root-cause the two zero-variance manual-lane lows: lookup-005
   and image-006 (score stability across runs suggests structural
   causes, like the DTC-navigation class fixed this week).
2. Characterise cross-006 and adversarial-006 (stable-low vs
   variance).
3. cross-004 spec-lookup completion (second read into the
   engine-specs table).
4. OBD lane enters maintenance: `harness27_obd_rerun.json` is the
   regression gate; next full run only after agent-affecting
   changes.
5. Umbrella #116 continues: next milestone eval after the above
   lands becomes v2.3.
