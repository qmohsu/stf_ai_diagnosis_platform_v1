# v2 Golden Set — Comparative Eval Section Plan

**Manual:** MWS-150-A (TRICITY155, Yamaha) — `0a2ba199-665f-41aa-a106-1163cad68d16`
**Source:** `tests/harness/evals/golden/v2/source/MWS-150-A.md` (~14,896 lines, ~1,080 parser-headings, 434 pages, zh-CN)
**Parser:** `app.harness_tools.manual_fs.parse_heading_tree` (post-APP-52)
**Drafted:** 2026-04-25 — REWRITTEN 2026-05-03 after pivot
**Status:** IN PROGRESS — 5/30 entries live on the dashboard (`dtc-001`, `lookup-001`, `cross-001`, `image-001`, `adversarial-001` — one per bucket).  25 additional bilingual candidates drafted 2026-05-16 in `candidates/batch_harness18.jsonl` (5 per bucket, HARNESS-18); pending team triage via `scripts/review_golden_candidates.py` before promotion to `mws150a.jsonl`.

---

## Why this file changed

Originally this plan was structured as a **domain-coverage** matrix (DTC / symptom / component / image / adversarial). After surveying the RAG path in Phase 1 of #74, it became clear that the more useful axis for a publishable comparison is **question type** — `lookup` vs `procedural` vs `cross-section` vs `image-required` vs `adversarial`. Different question types stress retrieval and synthesis differently, so they're the right axis for an agent-vs-RAG benchmark.

The previous plan and any drafts authored against it were discarded for three reasons:

1. **Manual was re-ingested twice** since drafting (UUID changed, then chunk_count changed again with APP-51/52). Old `manual_id` values in entries are invalid.
2. **Slug format changed** in APP-52 — was `p0117-p0118` (Latin-only, lowercase), now `故障代碼編號-p0117、p0118` (CJK preserved). Every existing slug citation is broken.
3. **Schema changed** — `GoldenEntry` now requires `question_type` and `expected_recall_slugs`. Old entries fail validation.

---

## Distribution target — 30 entries

Primary axis is `question_type`. Secondary axis (`category`: `dtc` / `symptom` / `component` / `image` / `adversarial`) is logged on each entry for sub-analysis but doesn't drive bucket counts.

The original plan targeted 8/8/6/4/4 across buckets; HARNESS-18 (#84) re-balances to a flat **6/6/6/6/6** floor so every bucket has enough surface area for inter-rater agreement work.  All 25 newly-drafted candidates live in `candidates/batch_harness18.jsonl` as bilingual EN + 繁體 entries pending team triage.

| `question_type` | Target (HARNESS-18) | Live on dashboard | Drafted candidates (batch_harness18) | Why this bucket |
|---|---|---|---|---|
| `lookup` | 6 | 1 (`lookup-001`) | 5 (`lookup-002`…`-006`) | Single-fact retrieval ("torque spec for X", "what does DTC P0117 mean") — the natural sweet-spot for RAG. If RAG can't win or tie here, it can't win anywhere. |
| `procedural` | 6 | 1 (`dtc-001`) | 5 (`procedural-002`…`-006`) | Multi-step diagnostic flows. Tests whether the agent's tool-walking + section-reading can reconstruct a sequence RAG can only return as fragments. |
| `cross-section` | 6 | 1 (`cross-001`) | 5 (`cross-002`…`-006`) | Combine info from ≥2 slugs. Tests whether RAG can stitch facts across chunks (it can't) and whether the agent can navigate to multiple sections in one run (it can). |
| `image-required` | 6 | 1 (`image-001`) | 5 (`image-002`…`-006`) | Answer needs the actual image bytes (terminal pinout, cable routing, alignment marks, balancer position). Marker-generated text descriptions don't substitute. RAG fails by definition; we measure HOW it fails. |
| `adversarial` | 6 | 1 (`adversarial-001`) | 5 (`adversarial-002`…`-006`) | Manual cannot answer (fake DTC, out-of-scope component, false-premise question). Agent should correct the premise rather than refuse blankly. RAG returns nearest-but-wrong with high confidence — an interesting failure mode. |
| **Total** | **30** | **5** | **25** | |

Six is a **floor**, not a ceiling. If a specific topic naturally surfaces more useful questions, that bucket grows; under-represented buckets get topped up by hand later.

---

## Authoring & validation workflow per entry

For each entry:

1. Pick a target slug (or set of slugs for `cross-section`) from the manual's heading tree.
2. Read the section text in full from the local manual copy.
3. Write the `question` as a technician would phrase it.
4. Write the `golden_summary` as a faithful synthesis of the source.
5. Pick `must_contain` (2–6 strings) — verbatim substrings of the source, verified mechanically.
6. Pick `pitfall_directives` (2–4 natural-language "don't" instructions) — each directive describes a specific failure mode the system MUST NOT exhibit (cross-domain confusion, fabricated wire colour, wrong DTC family, etc.).  Evaluated by the LLM judge for context-aware violation detection.  Replaces the old substring-based `must_not_contain` (which couldn't tell "is X" from "is NOT X").
7. Pick `golden_citations` (3–5 verbatim quotes from the source).
8. Set `expected_recall_slugs` (the slugs a system MUST surface to be considered correct).
9. Run `eval_one_golden.py --system both` against the draft.
10. If both systems' grades come back interpretable (no judge failures, deterministic-metric values are reasonable), promote to `v2/mws150a.jsonl`.
11. Note any surprises (e.g., "RAG actually beat the agent on this" or "the judge scored answer_quality=0.4 because the answer was correct but rambly").

The eval driver runs ~25 seconds per entry (agent + RAG + judge calls), costs ~$0.002. ~13 minutes + ~$0.06 for all 30.

---

## Open questions before authoring starts

1. **Should the `lookup` bucket include questions where both the agent and RAG can plausibly find the answer fast?** Or do we deliberately skew lookups toward "RAG should excel" cases (single-chunk facts in narrative prose) to give RAG its best shot?
2. **For `cross-section`, how do we pick slug pairs?** Random pairing within the same chapter, or curated "real diagnostic scenarios" (e.g., "list every DTC related to coolant temperature")?
3. **For `image-required`, how do we phrase questions that demand image-bytes?** "What is the wire color at terminal 3 in the wiring diagram?" — but the answer might be readable from Marker's text caption. Need to find figures whose meaning genuinely doesn't survive caption-only.

These don't block starting on `lookup` and `procedural` (which are clearer). I'll surface specific picks before going far on `cross-section` and `image-required`.

---

## Provenance / rebuild instructions

If anyone needs to recreate the local manual copy:

```bash
scp polyu-gpu:/home/talon/.local/share/containers/storage/volumes/\
infra_diagnostic_api_manuals/_data/MWS-150-A/\
0a2ba199-665f-41aa-a106-1163cad68d16.md \
diagnostic_api/tests/harness/evals/golden/v2/source/MWS-150-A.md
```

The source file is gitignored (`source/.gitignore` excludes `*.md`, `*.txt`, `images/`) because re-ingestion changes content and we don't want stale copies in version control.

The agent and RAG both run on the server's container, where the manual is mounted from the production volume — eval results are always against the current production data.
