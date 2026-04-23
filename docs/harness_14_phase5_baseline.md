# HARNESS-14 Phase 5 — Baseline Eval (2026-04-23)

First real `pytest --run-eval` run against the 10 v1 golden entries.

## Setup

| Component | Value |
|---|---|
| Corpus | MWS150-A (TRICITY 155 zh-CN, 434-page service manual) |
| Golden set | `golden/v1/mws150a.jsonl` — 10 entries (3 image, 2 component, 3 symptom, 1 dtc, 1 adversarial) |
| Agent | local `qwen3.5:27b-q8_0` on Ollama, 2× RTX 6000 Ada |
| Judge | `z-ai/glm-5.1` via OpenRouter, temp 0, JSON mode |
| Wall-clock | 27 min (2.7 min / entry avg) |
| GPU util during run | 38–50 %, 30 GB VRAM / card |

## Scores

3 / 10 passed the 0.7 threshold. Mean overall 0.534.

| ID | overall | section | recall | halluc | cite | traj | iters | stopped |
|---|---|---|---|---|---|---|---|---|
| image-002 | 0.20 | 0 | 0.00 | 0 | 0 | 0 | 6 | timeout |
| image-009 | 0.80 | 1 | 1.00 | 1 | 1 | 0 | 8 | complete |
| image-010 | 0.28 | 0 | 0.25 | 0 | 0 | 0 | 8 | max_iter |
| adversarial-001 | 0.60 | 1 | 0.00 | 0 | 0 | 0 | 8 | max_iter |
| component-005 | 0.60 | 0 | 1.00 | 0 | 1 | 0 | 8 | complete |
| component-006 | 1.00 | 1 | 1.00 | 0 | 1 | 0 | 7 | complete |
| symptom-003 | 0.60 | 0 | 1.00 | 0 | 1 | 0 | 6 | complete |
| symptom-006 | 0.20 | 0 | 0.00 | 0 | 0 | 0 | 8 | max_iter |
| symptom-013 | 0.28 | 0 | 0.25 | 0 | 0 | 0 | 8 | max_iter |
| dtc-002 | 0.80 | 1 | 1.00 | 1 | 1 | 1 | 6 | complete |

## Failure patterns

1. **Slug-opacity false negatives** (component-005, symptom-003). Agent finds the right section content but cites a different opaque slug than the golden. `fact_recall=1.0` but `section_match=0`. Root cause: Chinese headings produce non-semantic slugs (`-162`, `span-id-page-148-5-span`) that vary by sampling path for the same content.
2. **Iteration-budget exhaustion** (image-010, symptom-006, symptom-013). 8 iterations too tight for the agent's tool-call style — frequent `search_manual` retries before reading. Agent emits no final JSON, `fact_recall` low despite partial investigation.
3. **Adversarial phrase mismatch** (adversarial-001). Agent correctly declined to fabricate but did not use the literal "not found" phrase required by `must_contain`.
4. **Trajectory always failing** (7/10). Expected tool traces specify 2 tools; agent uses 5–8. Either the goldens under-estimate realistic tool use or the agent is inefficient.

## Judge anomalies

- `hallucination=1` on two runs (image-009, dtc-002) despite the judge's own reasoning stating no hallucinated content appeared. GLM 5.1 occasionally misfires on this rubric field.

## Intended next iterations

Ordered by expected impact / cost:

1. System-prompt tweaks in `manual_agent_prompts.py`: require "Not found:" literal phrase for unanswerable questions; discourage repeated `search_manual`; prefer `get_manual_toc` → `read_manual_section` flow.
2. Raise `ManualAgentConfig.max_iterations` 8 → 12 and `max_tokens` 12288 → 16384.
3. Relax golden `expected_tool_trace` counts (4–6 instead of 2) to match realistic agent behaviour.
4. (Longer-term) Slug-tolerant section matching in the judge rubric: accept any agent citation whose section text contains a golden quote.

After each tweak, re-run `pytest --run-eval` and compare against this baseline.
