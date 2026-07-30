# Weekly Report — 2026-07-21 → 2026-07-30

**Author:** Xiangzhu Yan
**Scope:** HARNESS-30 end-to-end (manual storage & index rebuild), from
root-cause to production cutover and closure.

## 1. Headline

**HARNESS-30 opened, executed, and CLOSED in nine days.** The manual
lane moved from a runtime-parsed, quality-ungated heading tree to a
spec-driven storage + validated-index architecture, live in
production with a one-switch rollback. The 30-golden benchmark moved
**0.831 → 0.891 (30/30 pass, first all-green run)**, and the
second manual (Toyota Corolla E11, English) onboarded with **zero
schema/invariant changes** — the scalability motive of the project,
measured rather than claimed.

| Lane | Milestone |
|---|---|
| **Manual agent** | New regression reference **0.891** (median 0.918, σ 0.091, 0 timeouts; `harness30_fair_baseline_20260729.json`), superseding 0.831. Four chronic lows (cross-006 0.483, lookup-005 0.578, image-006 0.612, adversarial-006 0.646) all resolved to 0.86–0.98. |
| **Architecture** | PDF → (content.md + index.yaml) pipeline: PyMuPDF text authority (100% recall by construction), MinerU geometry advisor, region rescue, I0–I8 build gates with mutation-tested validators. Spec `manual_index_spec.md` v0.5. |
| **Production** | Cutover approved (checkpoint B) and deployed 07-29; both manuals on the index track; `MANUAL_INDEX_TRACK=off` rollback path verified. Upload channel migrated to two-stage onboarding (marker quick layer + automatic index-track upgrade). |
| **Repo** | 7 PRs merged (#219 spec, #221 plan, #222 Phase 0, #223 Phase 1, #224 Phase 2, #226 Phase 3, #229 Phase 4) + 2 docs PRs (#227 baseline, closure pending); #218 closed; issues filed: #220 (scanned-PDF trigger), #225 (eval runtime), #228 (viewer). |

## 2. What was built (phases, all merged)

- **Phase 0 (#222)**: absence-claim guard — `search_manual_text`
  literal grep + SEARCH GATE ("never claim absence without a 0-match
  search"). Shipped ahead of the rebuild; alone lifted the legacy
  track 0.831 → 0.871.
- **Phase 1 (#223)**: storage pipeline — authority-separation
  composition; TRICITY155: 12,200 lines, 0 missing, 48 regions
  rescued (40 recovered as structured tables via `find_tables`).
- **Phase 2 (#224)**: index builder — 496-node validated sidecar,
  22/22 DTC entity cards, controlled vocabulary, DeepSeek summaries
  behind mechanical gates; 12-mutation validator-of-the-validator.
- **Phase 3 (#226)**: runtime dual-track + golden makeup (25/25
  anchors, expert semantics untouched) + A/B (legacy 0.871 vs index
  0.877/0.895) + 20/20 table probes (enhancement ladder rungs 2–4
  dormant).
- **Phase 4 (#229)**: Corolla E11 through the whole pipeline
  (1,264 nodes, gates green, schema unmodified; marginal cost = 1 new
  rule + 1 generalized rule + vocab aliases); onboarding runbook;
  two-stage upload worker.

## 3. Closure work (this report's tail)

- Golden makeup regenerated against the R6 tree: **25/25 pure
  title-exact** (no fallback strategies needed anymore).
- procedural-005 verified at **0.997** post-R6; cross-005 remains the
  variance-prone straggler (~0.61–0.72 band; reachability root cause
  eliminated, residual is two-part answer completeness — noted, not
  chased).
- Summary language gate (mechanical): 348/496 English-on-CJK
  summaries detected and regenerated with a hard per-call language
  instruction; gate applies to generation AND cross-rebuild reuse.
- Experiment environment retired: marker/Docling venvs and outputs
  removed (~10.8 GB freed); MinerU + audit venvs promoted to
  production build dependencies.

## 4. Risks & honest notes

- Index-lane benchmark advantage over "legacy + Phase 0" is modest
  (0.891 vs 0.871); the cutover case rests on content completeness
  (+7.3%), maintainability (build-time gates vs production
  discovery), scalability (measured on Corolla), and refusal quality
  — an architecture investment, stated as such at checkpoint B.
- marker remains only as the upload quick-layer; its output is
  superseded per-manual as each sidecar lands.
- Scanned PDFs remain out of scope (placeholder #220).

## 5. Next

1. **HARNESS-31 (#225)**: cut full-eval runtime ~55 min → <20 min
   (separate session).
2. **#228**: dashboard manual viewer consumes index.yaml.
3. Observation period: watch production index-track behaviour;
   rollback switch stays armed.
