# Manual Storage & Index Specification (v0.3 — DRAFT for review)

| | |
|---|---|
| Status | **DRAFT** — under active review with user, not yet ticketed |
| Author | Xiangzhu Yan |
| Date | 2026-07-23 (v0.1 2026-07-23, v0.2 same-day: storage/index decoupling; v0.3 same-day: storage requirements S1–S5, authority-separation architecture, golden makeup procedure, validator trustworthiness) |
| Supersedes | runtime heading-tree parsing in `app/harness_tools/manual_fs.py` as the *index source*; the module's reader utilities remain |
| Evidence base | Defect scan 2026-07-21 (5 defect classes); cross-006 root cause; engine bake-off + fidelity audits 2026-07-22/23 (marker 1.10 / marker 2.0 / MinerU 3.4 medium+high / Docling) |

## 0. Design principles

1. **Storage and index are decoupled.**  The content file is pure
   storage and makes NO structural claims; heading marks in it are
   cosmetic text.  ALL navigation structure lives in the index sidecar.
2. **Authority separation.**  For born-digital PDFs the text layer
   (PyMuPDF) is the *text authority* — 100% recall by definition.
   AI layout engines are *geometry advisors* only (region types,
   reading order, table structure); they are pluggable, low-trust, and
   an engine error can cost layout quality but never content.
3. **Completeness by construction, verified by gates.**  Extraction is
   composed so that S2/S3 hold structurally (union + rescue passes);
   the audit then verifies an equality, not a hope.
4. **The index is a first-class, validated, rebuildable artifact** —
   never a runtime parse by-product.

## 1. Storage layer

### 1.1 Requirements (user-specified, 2026-07-23)

| # | Requirement | Acceptance |
|---|-------------|-----------|
| S1 | Input PDF → output Markdown (AI-consumption storage format) | format contract |
| S2 | 100% text recall — non-negotiable | line-level reconciliation audit (I0, exact) |
| S3 | Images cropped "just right" (too large swallows body text; too small fragments figures), stored externally, linked inline at original position | region-size policy + page-level coverage audit |
| S4 | Final md inherits 100% of the PDF's text AND image information | S2+S3 combined gate |
| S5 | Index-friendly: flat, addressable, typed | normalized item stream (§3.4) |
| Env | Local, rootless-Podman-compatible, pip-installable, workshop-grade | no Docker daemon / external binary deps |

### 1.2 Measured engine baseline (TRICITY155, 434 pp, audits 2026-07-23)

| candidate | char recall | numeric recall | image pages | tables | catastrophic loss mode |
|---|---|---|---|---|---|
| marker 1.10 (prod) | 92.7% | 83.7% | 199 | pipe | text loss on diagram pages |
| marker 2.0 fast+no-OCR | 89.7% | 81.7% | 228 | pipe | worst text recall; degrades to page images (safe-ish) |
| MinerU 3.4 hybrid medium | 94.5% | 83.7% | 216 | HTML (rich) | **48 silently-EMPTY table items** (spec tables, brake exploded views, P0335 area) |
| MinerU 3.4 hybrid high | — | — | — | — | **FAILED Env**: internal task timeout after 40+ min on shared GPU (2026-07-23); not workshop-grade repeatable |
| Docling (±OCR identical) | 79.6% | **99.8%** | 985 imgs | pipe (1,899 lines) | drops unclassifiable prose blocks (166 pages ≥5 missing lines) |

Conclusions: **no engine meets S2 alone**; every engine has a distinct
catastrophic-loss mode; Docling's table fidelity is best (numeric
99.8%), MinerU's prose+inline-image discipline is best (881 imgs, 1:1
inline refs), marker's failure degradation is safest.  Hence §1.3.

### 1.3 Composition pipeline (engine-agnostic guarantee)

```
①  geometry pass   — layout engine (pluggable) proposes regions:
                     figure / table / prose blocks + reading order
②  text authority  — PyMuPDF extracts ALL text lines per page
                     (ground truth for born-digital; per-page OCR
                     fallback flagged for scanned pages)
③  composition     — md assembled in reading order; text content
                     ALWAYS from ②; tables from engine when its
                     cell-structure output is non-empty, else rescue
④  region rescue   — any region with empty/missing engine output is
                     rendered from the PDF at 150 dpi via its bbox and
                     linked inline (demonstrated on p146: recovered
                     diagram + parts table lost by both engines)
⑤  dual representation — figure regions containing extractable text
                     keep BOTH the rendered image (labels visible) and
                     the text lines (searchable); resolves S3's
                     too-big/too-small dilemma
⑥  reconciliation  — I0 audit: every baseline text line must appear in
                     the final md; any miss FAILS the build (equality,
                     not sampling)
```

Engine choice becomes a quality knob (how often rescue triggers), not
a correctness decision.  Note that in this composition the engines'
raw *numeric/text recall* differences largely vanish — text always
comes from the authority layer — so the substrate is chosen on
**geometry quality** (placement, inline-image discipline, table
structure) and **Env stability** alone.

**Substrate decision (2026-07-23): MinerU 3.4.4 hybrid-engine medium
as the sole geometry substrate.**  Rationale: best prose geometry +
strictest inline-image discipline (881 imgs, 1:1 refs) + stable 25-min
runs; its 48 empty-table regions are exactly what the rescue pass
(④) exists for.  effort-high failed Env (internal timeout, shared
GPU).  Docling is shelved as an optional *table-structure authority*
upgrade (numeric-in-table fidelity 99.8%, pipe tables) if M2/M3 evals
show structured-table gaps; its adapter slots into §3.4 without
schema change.  marker is retired.

## 2. Index architecture

```
content items ──repair──> logical tree ──enrich──> classified nodes
              ──validate──> index.yaml sidecar (gates; fail = no publish)
```

(Repair/enrich/validate as §5/§3/§6.)

## 3. Index schema (sidecar `index.yaml`, Pydantic-validated)

### 3.1 Top level

```yaml
spec_version: "0.3"
manual_id: "0a2ba199-…"
source:
  content_file: "0a2ba199-….md"
  content_sha256: "…"
  parser: "<engine+version+mode>"
  built_at: "…"
applicability:
  manufacturer: "Yamaha"
  models: ["TRICITY155", "MWS150-A"]
vocab_version: "1"
tree: [ <IndexNode>, … ]
entities:
  faults: [ <FaultEntity>, … ]
```

### 3.2 IndexNode

```yaml
node_id: "elec-fault-p0335"      # stable; §4
title: "故障代碼編號 P0335"
aliases: ["P0335"]               # legacy slugs land here (§8)
node_type: "fault-isolation"     # description | operation | inspection |
                                 # remove-install | troubleshooting-tree |
                                 # fault-isolation | specification |
                                 # wiring | parts | index
subsystem: "electrical"          # controlled vocab (§3.5)
span: {start_item: 6112, end_item: 6139}
page_range: [350, 352]
summary: "…"                     # OPTIONAL in v0.x — deferred post-M3
children: []
```

### 3.3 FaultEntity (S1000D fault-model shape)

```yaml
code: "P0335"
item: "曲軸位置感知器(未收到…訊號)"
symptom: "引擎無法起動"
fail_safe: "無法運轉"
detect_ref:  "elec-desc-self-diag-table"
isolate_ref: "elec-fault-p0335"
correct_ref: "elec-fault-p0335"
related_refs: ["troubleshooting-no-start"]
```

Absent optional fields mean "manual genuinely lacks it" — I3
distinguishes that from "extraction missed it" via the full-text sweep.

### 3.4 Normalized item stream (adapter contract)

Adapters map any engine's output to `{idx, page, kind, text|html,
bbox}` with `kind ∈ {para, title-candidate, table, image, page_header,
noise}`.  Spans index into THIS stream.  This seam is the technical
expression of principle 0.1 — the index layer never sees a native
engine format.

### 3.5 Controlled vocabulary (separate versioned `vocab.yaml`)

~15–20 subsystems for scooters/light vehicles + `node_type` list +
per-subsystem alias strings (電裝系統 → electrical …).  Per-language
aliases extend it for new manuals (e.g. English Haynes).

## 4. Stable node IDs

`{subsystem}-{type-abbrev}-{semantic-slug}` (+`-2/-3` deterministic
suffixes).  Derived from classified meaning, not text position — engine
swaps and re-parses keep IDs stable while content persists.  DTC nodes
canonical: `{subsystem}-fault-{code}`.  Retired IDs tombstone, never
recycle.

## 5. Repair rules (deterministic, ordered, unit-tested per rule)

- **R1 noise demotion**: banners incl. suffixed (`警 告 EWAxxxxx`,
  `注 意 ECAxxxxx`), flowchart tokens (`OK↓`, `▲/▼` runs),
  full-sentence caution lines → `noise` kind, folded into enclosing
  node body (never dropped).
- **R2 DTC heading synthesis**: `^故障代碼編號\s+([PCBU]\d[0-9A-F]{3})`
  in para/table-cell with no existing title-candidate → synthetic
  `fault-isolation` node (recovers all 13 orphaned DTCs; parse from
  HTML tables where applicable).
- **R3 troubleshooting nesting**: known cause-group titles (vocab
  aliases) attach under nearest preceding symptom title.
- **R4 chapter assembly from page headers**: running-header items
  determine chapter membership (fixes inverted parents).
- **R5 known-structure templates**: self-diag table, DTC index, spec
  tables get fixed `node_type` by table-header patterns.

## 6. Validation invariants (build-failing gates)

| ID | Invariant | Guards |
|----|-----------|--------|
| I0 | **Parse completeness (exact)**: every baseline text line (PyMuPDF, furniture-filtered) present in final md; every engine-proposed region non-empty or rescued | upstream content loss |
| I1 | Every non-noise stream item in exactly one leaf span; sibling spans contiguous, non-overlapping | orphaned content |
| I2 | No empty shells (leaf body ≥ 50 chars or `node_type: index`); every parent ≥ 1 child | flattened hierarchy |
| I3 | Every DTC from full-text regex sweep appears as FaultEntity with resolving `isolate_ref` | unnavigable DTCs |
| I4 | node_ids unique; all refs resolve; acyclic; single root per chapter | dangling refs |
| I5 | No node title matches R1 noise patterns | junk headings |
| I6 | Every node has `subsystem`+`node_type` from vocab (summary optional in v0.x) | enrichment gaps |
| I7 | `content_sha256` matches content file | index drift |
| I8 | **Noise budget**: per-page noise ratio ≤ threshold; all noise items listed in build report | R1 over-matching |

### 6.1 Build report (human-auditable artifact)

Every build emits: coverage stats, rescue-triggered regions (count +
pages), noise item samples, unmapped entities, rule-hit counts.
Reviewed on spot-check basis; required attachment for index publication.

### 6.2 Validator trustworthiness

- **Gates are mechanical only** — no LLM in any gate; enrichment LLM
  output is gated by mechanical checks (non-empty, vocab membership).
- **Independent second channels** — I0 uses PyMuPDF (not the pipeline's
  own output); I3 sweeps raw text (not the entity table).
- **Fail-closed asymmetry** — thresholds conservative; false alarm
  costs a rebuild, a miss costs silent production error.
- **Mutation tests**: each invariant has fixture indexes with its
  defect class deliberately injected; the gate MUST catch them (tested
  in `tests/index_spec/`).
- **Ratchet protocol**: any production incident traced to an index
  defect that passed the gates REQUIRES adding an invariant that would
  have caught it, alongside the fix.
- **Honest boundary**: gates verify structure, not semantics.  Semantic
  errors (wrong-but-plausible parent, bad summary) are caught by the
  golden eval (M2/M3 gates) and build-report spot-checks.

## 7. Tool-layer integration (dual-track)

- `get_manual_toc`: sidecar present → render from `tree` + DTC quick
  index generated from `entities.faults` (complete by I3); else current
  behavior.
- `read_manual_section`: accepts `node_id`, alias, or legacy slug;
  substring fallback only within same subsystem, listing all matches.
- **Absence-claim guard (M0, ships first, engine-independent)**: DTC
  with body occurrences but no navigation target → tools answer
  "present in manual, N occurrences, no indexed section — use full-text
  search"; agent prompt gains "never claim the manual lacks X without a
  full-text search".

## 8. Golden migration ("makeup" — asset-preserving)

The 30 locked manual goldens embody expert judgment bought with
workshop time; migration MUST NOT spend that asset again.

**Field split (measured on `locked/mws150a.jsonl`):**

| layer | fields | action |
|---|---|---|
| Semantic asset (untouchable) | `question(_zh)`, `golden_summary(_zh)`, `pitfall_directives`, `category`, `difficulty`, `requires_image`, `notes` | zero changes |
| Positional anchors | `golden_citations` (24), `expected_recall_slugs` (24), `expected_tool_trace` (30, slug args) | automated remap |
| Content-literal strings | `must_contain` (30) | verify literal presence in new content; character-variant fixes only, flagged for human confirm (opportunity to clean legacy mojibake transcription artifacts) |

**Procedure:** (1) archive current locked file immutably, new file
carries provenance (source sha + remap-table version); (2) automated
slug → node_id remap by title-text matching, remap table itself
reviewed; (3) must_contain literal sweep with old/new diff for human
confirmation; (4) A/B eval old vs new pipeline — stable deltas must be
attributed before cutover; (5) **experts are not re-engaged** — first
KPI of the migration is preserving their investment.  Same procedure
applies to future storage/index upgrades and to the OBD locked tier if
ever affected.

## 9. Rollout

| Milestone | Content | Gate |
|-----------|---------|------|
| M0 (= HARNESS-30a) | Absence-claim guard + R1 regex extension in current pipeline | unit tests; adversarial/dtc goldens no regression |
| M1 | Storage pipeline (§1.3) + audits as CI-style checks; substrate decision from final scoreboard | S2 exact pass; S3 coverage report reviewed |
| M2 | Schema + invariant suite + build CLI + R1–R5 on TRICITY155 | all gates green; cross-006 + 4 stable lows re-run |
| M3 | Tool dual-track + golden makeup; full 30-golden A/B | means over 2–3 runs comparable (no stable per-golden regression; single-run deltas < judge-variance band are noise, not failures) |
| M4 | Corolla Haynes; new-manual runbook | schema+invariants unmodified; rule additions allowed and counted in build report (per-manual marginal cost is the scalability metric) |

Parallel (week 1): M0 ticket; MinerU-high / final scoreboard; root-cause
diagnosis of lookup-005 / image-006 / adversarial-006 (may reveal
non-index work items).

## 10. Open questions

1. ~~Final substrate choice~~ **RESOLVED 2026-07-23**: MinerU medium
   sole substrate; Docling shelved as table-authority upgrade; marker
   retired (§1.3).
2. ~~Table format~~ **RESOLVED**: HTML tables (MinerU) in content;
   renderer adapts; rescue regions carry image + flat text.
3. Scanned-manual path (future): per-page OCR fallback policy — out of
   scope v0.3, flagged for the first scanned manual.
