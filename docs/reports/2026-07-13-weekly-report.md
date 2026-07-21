# Weekly Report — Manual-Agent Benchmark: from v1 to v2.1

**Author:** Xiangzhu Yan
**Date:** 2026-07-13
**Scope:** This week we focused on improving the manual agent
according to the v1 evaluation result: every problem the v1
benchmark surfaced was filed as a GitHub issue, fixed in its own
PR with a measured before/after score, and merged only on that
evidence.

---

## 1. Headline scores

| Milestone | Mean overall (30 locked goldens) | Notes |
|---|---:|---|
| **v1** (2026-06-20) | 0.590 | Confounded: 19/30 runs hit the time budget; metric artifacts under-counted correct answers. NOT directly comparable to v2+. |
| **v2** (2026-07-12) | 0.670 | Clean re-baseline after the eval-harness fixes; 0 timeouts. |
| v2 re-scored under today's rubric | 0.713 | Same v2 transcripts, current grader — the fair baseline for the v2.1 delta. |
| **v2.1** (2026-07-13) | **0.777** | This week's agent + grader fixes combined. Median 0.811, σ 0.195, 0 timeouts, mean wall 73 s. |

**Same-rubric agent improvement this week: 0.713 → 0.777
(+0.064).** Raw journey since v1: 0.590 → 0.777. The agent beats
the RAG floor (0.239 at v2) on every question type.

### Per question type (v2 → v2.1)

| Type | v2 | v2.1 |
|---|---:|---:|
| lookup | 0.805 | 0.839 |
| procedural | 0.567 | **0.741** |
| cross-section | 0.608 | **0.752** |
| image-required | 0.660 | 0.779 |
| adversarial | 0.711 | 0.774 |

---

## 2. Problems found → fixes shipped → measured effect

| Problem (short) | Issue / PR | Measured effect |
|---|---|---|
| Agent ran out of time/iterations on 19/30 questions | #143, #144, #165 / PR #163, #166, #164 | Timeouts 19/30 → **0/30**; mean wall 66 s vs 240 s budget |
| Judge could not credit a correct adversarial refusal | #146 / PR #169 | Adversarial declines now scored: answer_quality 0 → 0.75–0.95; generic decline A/B 0.25 → 0.45 |
| "Must not omit X" counted as hallucination (double penalty) | #147 / PR #170 | Omissions moved to recall side; RAG hallucination_penalty 0.71 → 0.82 |
| Slug-spelling mismatches zeroed correct citations | #145 / PR #168 | citation_quality 0.427 → 0.733 (v1→v2 component) |
| Adversarial entries got a free +0.20 score floor | #148 / PR #176 | section_recall → N/A + weight renormalisation; floor removed |
| CJK-exact `must_contain` under-counted correct English answers | #149 / PR #174 | Bilingual fact matching (fact_recall 0.336 → 0.678 v1→v2, with completions) |
| Golden tool-count expectations unrealistic | #150 / PR #172 | trajectory_efficiency 0.363 → 0.820 |
| Golden manual identity drifted from corpus labels | #151 / PR #173 | UUID-only gate at promotion; drift class eliminated |
| Agent made redundant tool calls | #152 / PR #171 | Mean tool calls 6.17 → 5.77 |
| Free structural-floor weight terms inflated both systems | #153 / PR #175 | Zero-content floor 0.40 → 0.30 |
| Re-baseline needed after all rubric fixes | #155 / PR #177 | **v2 pinned: agent 0.670 vs RAG 0.239**; thresholds re-pinned 0.4 / 0.1 |
| Long procedures lost steps; near-misses became "manual doesn't contain X" | #184 / PR #185 | procedural 0.567 → 0.709; dtc-001 0.338 → 0.993 (smoke) |
| Real section title invisible to TOC (marker-pdf missed heading) | #186 / PR #187 | procedural-005 0.365 → 0.988; image-005 0.382 → 0.918 |
| claim_precision punished valid alternate-section citations | #192 / PR #199 | claim_precision 0.294 → 0.660; overall +0.043 (exact offline re-score) |
| Multi-part questions half-answered; wrong-manual reads discovered | #194 / PR #196 | cross-section 0.624 → 0.724 (3 verify rounds); deterministic manual-pinning guard added |
| Figure-caption headings shadowed real chapters | #195 / PR #198 | `前煞車` 136-char stub → full 27 kB chapter; zero golden slugs lost |
| Judge cannot see figures the agent surfaced | #193 / PR #197 — **HELD** | Plumbing complete, no regressions; merge held because the score lift is unproven (confounds filed as #200–#202) |

Supporting artifacts: every PR carries its verification report;
raw run data committed under `docs/eval-reports/`
(`phase6_rebaseline_v2_eval.json`, `harness24_wp1_manual_lane.json`,
`phase6_v2_1_manual_lane.json`).

---

## 3. Honest caveats on v2.1

- **Per-entry run variance remains the dominant noise source**
  (σ ≈ 0.19): e.g. dtc-001 scored 0.99 in two verification runs
  but 0.37 in this one (navigation stochasticity at temp 0.2).
  Milestone means are trustworthy; single entries are not.
- **3 entries below the 0.4 floor this run:** cross-004 (the only
  golden that never names its vehicle, so the new pinning guard
  cannot engage — filed as #204), dtc-001 (variance, above),
  procedural-002 (stubborn navigation case).
- v1 → v2 numbers are not same-rubric comparable (the grader was
  itself part of what we fixed); the same-rubric weekly delta is
  the 0.713 → 0.777 figure.

## 4. Next steps

1. Expert review of the 15 OBD goldens at `/goldens/obd` (#191) —
   the OBD lane hit 100 % pass after one tuning round (#117) but
   its goldens are still candidate-tier.
2. Variance reduction + the held image-evidence PR: #200
  (intermittent declines), #201 (judge parse fallback), #202
  (vision descriptions at ingestion), then re-verify PR #197.
3. #204 (cross-004 vehicle naming), #203 (pre-existing router
   test failure).
4. Umbrella #116 continues: next milestone eval after the above
   lands becomes v2.2.
