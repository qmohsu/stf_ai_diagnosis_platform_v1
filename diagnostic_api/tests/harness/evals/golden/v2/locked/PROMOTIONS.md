# Locked-tier promotion log (HARNESS-20)

This file is the append-only audit trail for the locked tier of
the V2 golden corpus.  Every line in `locked/*.jsonl` corresponds
to exactly one row here.

## When a row is added

`scripts/promote_golden.py` appends one row whenever an entry is
promoted from the mutable candidate set
(`tests/harness/evals/golden/v2/*.jsonl`) into the canonical
locked set (`tests/harness/evals/golden/v2/locked/*.jsonl`).  The
eval harness (`tests/harness/evals/test_manual_agent_eval.py`)
reads only the locked tier, so promoting an entry is what makes
it count against the agent-vs-RAG benchmark.

Promotion requires that the most-recent expert review on the
entry is `status='accept'` with `star_rating >= 4`, unless the
promoter passes `--force` (which is then noted in the `reason`
column).

## Why this file is plain Markdown

A DB table would be invisible during code review and would let
anyone with `psql` access rewrite history.  A flat audit file in
the repo means every promotion shows up as a git diff and is
permanently attributable via `git blame`.

## Row format

| Column | Meaning |
|---|---|
| `promoted_at` | UTC ISO-8601 timestamp produced by the script. |
| `entry_id` | The golden entry's stable id (e.g. `<manual_uuid>-dtc-001`). |
| `content_hash` | SHA-256 of the canonical-form (`sort_keys=True`) JSON of the promoted line, hex-encoded.  Lets a future tool detect whether the locked line ever drifts away from what was originally promoted. |
| `reviewer` | Free-text label for the human who ran the promotion (e.g. `talon`, `talon@polyu`).  Promoter, not the workshop expert — the expert's stars live in `golden_reviews`. |
| `expert_review_id` | UUID of the `golden_reviews` row whose acceptance triggered this promotion.  Resolves the audit chain back to the expert grade.  May be omitted when `--force` was used; the `reason` column then carries the justification. |
| `reason` | Short free-text explanation.  Mandatory.  Examples: `"expert ≥4★ on 2026-05-23"`, `"force-promoted to unblock #74; expert review pending"`. |

## Rows

<!-- Appended by promote_golden.py.  Do not edit by hand; the
     content_hash protects locked entries from silent edits, and
     editing this file would only mask drift. -->

| promoted_at | entry_id | content_hash | reviewer | expert_review_id | reason |
|---|---|---|---|---|---|
| 2026-05-24T13:22:15+00:00 | 0a2ba199-665f-41aa-a106-1163cad68d16-adversarial-001 | `38980e4bb95e87b45a92d514a23dbe46f2e78333cde00d1c5360c7f5dea96f28` | talon | 827773d4-d850-4795-ab45-6366f4106228 | HARNESS-20 phase 2 retro-lock: Towngas expert 5* accept (review id stamped explicitly via --expert-review-id) |
| 2026-05-24T13:22:15+00:00 | 0a2ba199-665f-41aa-a106-1163cad68d16-adversarial-002 | `8cea56aa49123de808729d71df96f2367a7de3c1e8084a3f19e3b32d87a4b0d6` | talon | cab539a6-580e-4e9b-bb7b-d44a10351d9d | HARNESS-20 phase 2 retro-lock: Towngas expert 5* accept (review id stamped explicitly via --expert-review-id) |
| 2026-05-24T13:22:15+00:00 | 0a2ba199-665f-41aa-a106-1163cad68d16-adversarial-003 | `e5e3b04a7b5df8fee275d53b826c3a83e5b7b098780b3469378e2568ab866a55` | talon | f2612c90-7914-497e-8e48-fc381abea536 | HARNESS-20 phase 2 retro-lock: Towngas expert 5* accept (review id stamped explicitly via --expert-review-id) |
| 2026-05-24T13:22:15+00:00 | 0a2ba199-665f-41aa-a106-1163cad68d16-adversarial-004 | `235eacd58a3b65d7eeb8254a1f1482294f0ef124851f8961a833a2cc2d34bb5a` | talon | 8024e3c4-eb6f-423f-a651-cb14cfcc1e74 | HARNESS-20 phase 2 retro-lock: Towngas expert 5* accept (review id stamped explicitly via --expert-review-id) |
| 2026-05-24T13:22:15+00:00 | 0a2ba199-665f-41aa-a106-1163cad68d16-adversarial-005 | `abfcae6691c073d2528acc20b333ba1e20fef005a455276e492336f2e4f9d8b0` | talon | 9907a0ba-5b42-4383-b11f-1e228ebe2356 | HARNESS-20 phase 2 retro-lock: Towngas expert 5* accept (review id stamped explicitly via --expert-review-id) |
| 2026-05-24T13:22:15+00:00 | 0a2ba199-665f-41aa-a106-1163cad68d16-adversarial-006 | `9079af413c78241439b4e9408e19e8afca86ac5532fad178b4192e2a008e704f` | talon | 1d5cc854-05c2-4b2e-851a-7a5de1c80b79 | HARNESS-20 phase 2 retro-lock: Towngas expert 5* accept (review id stamped explicitly via --expert-review-id) |
| 2026-05-24T13:22:15+00:00 | 0a2ba199-665f-41aa-a106-1163cad68d16-cross-001 | `13d988f1cca9faaf845a556e68edea3a24ea61793ca1076d827532642cdf19ee` | talon | 334897a0-1609-4860-bf44-0f8a34ae2a17 | HARNESS-20 phase 2 retro-lock: Towngas expert 5* accept (review id stamped explicitly via --expert-review-id) |
| 2026-05-24T13:22:15+00:00 | 0a2ba199-665f-41aa-a106-1163cad68d16-cross-002 | `38351d1274af365cd0ed03850edec68058ce61d446825aa9b0a09f3416657ca6` | talon | 0ac918ba-02ca-47b6-ae47-4dab311a57e3 | HARNESS-20 phase 2 retro-lock: Towngas expert 5* accept (review id stamped explicitly via --expert-review-id) |
| 2026-05-24T13:22:15+00:00 | 0a2ba199-665f-41aa-a106-1163cad68d16-cross-003 | `152d2d2390a580cc3c8f55680b9cfc2b94d520a2c46abcb93a17a162ee8d292e` | talon | 772a2c8c-d19e-4421-932f-362932c2d995 | HARNESS-20 phase 2 retro-lock: Towngas expert 5* accept (review id stamped explicitly via --expert-review-id) |
| 2026-05-24T13:22:15+00:00 | 0a2ba199-665f-41aa-a106-1163cad68d16-cross-004 | `1a681b96afe62cee3fa4197ea88499c8da9624c2e0417767fb717e71b7e85592` | talon | 9edfa66d-edc0-421c-a967-4760145961cf | HARNESS-20 phase 2 retro-lock: Towngas expert 5* accept (review id stamped explicitly via --expert-review-id) |
| 2026-05-24T13:22:15+00:00 | 0a2ba199-665f-41aa-a106-1163cad68d16-cross-005 | `d8116ced68cc9e313dbe13c755d6ea632197d307a4f6b18bf54782256a045705` | talon | b7cc5ca8-2289-4d56-9bc0-38f627fe080c | HARNESS-20 phase 2 retro-lock: Towngas expert 5* accept (review id stamped explicitly via --expert-review-id) |
| 2026-05-24T13:22:15+00:00 | 0a2ba199-665f-41aa-a106-1163cad68d16-cross-006 | `a7c44b7ea6b8905571fd0ef1af5fd6950d2cddb41e287c74ab99401b19ea7c35` | talon | 32df382b-2b68-485e-9df8-106fb87b4cd4 | HARNESS-20 phase 2 retro-lock: Towngas expert 5* accept (review id stamped explicitly via --expert-review-id) |
| 2026-05-24T13:22:15+00:00 | 0a2ba199-665f-41aa-a106-1163cad68d16-dtc-001 | `9bfea9dee1c89bec2aef4c0f4484d1dbdc7c4c5100fbf14570e0d3938b3ae2ad` | talon | cfa65dd1-1a48-42e5-bb39-76ff639e6c2b | HARNESS-20 phase 2 retro-lock: Towngas expert 5* accept (review id stamped explicitly via --expert-review-id) |
| 2026-05-24T13:22:15+00:00 | 0a2ba199-665f-41aa-a106-1163cad68d16-image-001 | `dfecf6c9425c9ba8604756274e4b6380af9cd0399d3058e5d64cb37638b4c28e` | talon | 2239d58c-5578-491a-bcbf-85fc07a5daaa | HARNESS-20 phase 2 retro-lock: Towngas expert 5* accept (review id stamped explicitly via --expert-review-id) |
| 2026-05-24T13:22:15+00:00 | 0a2ba199-665f-41aa-a106-1163cad68d16-image-002 | `0f0cb284ed3b077c91ad8d24c3762b6b802a7963f466acf5460d3db7c2590a63` | talon | caecc854-d44b-4e55-960d-63a3ace95656 | HARNESS-20 phase 2 retro-lock: Towngas expert 5* accept (review id stamped explicitly via --expert-review-id) |
| 2026-05-24T13:22:15+00:00 | 0a2ba199-665f-41aa-a106-1163cad68d16-image-003 | `1361caf9bb1dd2888d577f539bbc8adc483ec1b108d03473b5b9f66458c52dc4` | talon | 9012145f-c141-43d9-84e0-b5e501c90383 | HARNESS-20 phase 2 retro-lock: Towngas expert 5* accept (review id stamped explicitly via --expert-review-id) |
| 2026-05-24T13:22:15+00:00 | 0a2ba199-665f-41aa-a106-1163cad68d16-image-004 | `c9aefe6d549b1890ce89bd4dc034353202d1ff255cd7aee49ba7b534e535ed3e` | talon | 626f9de6-9a84-454c-a757-a559bc5d1bbb | HARNESS-20 phase 2 retro-lock: Towngas expert 5* accept (review id stamped explicitly via --expert-review-id) |
| 2026-05-24T13:22:15+00:00 | 0a2ba199-665f-41aa-a106-1163cad68d16-image-005 | `893a9da78399cc22474bfdfc64dd7f9cd2f1b3528820017ce0dff54e9d1a5abb` | talon | 7a833211-6cda-434d-909c-5c4e6c8f07d5 | HARNESS-20 phase 2 retro-lock: Towngas expert 5* accept (review id stamped explicitly via --expert-review-id) |
| 2026-05-24T13:22:15+00:00 | 0a2ba199-665f-41aa-a106-1163cad68d16-image-006 | `43b89a4ae9279d14d32bb5dbb2be78887978a876b3b0f924d0dc0ff55e3e0f3e` | talon | 2315fd7c-3617-4c09-b9a4-51ca3a6f943b | HARNESS-20 phase 2 retro-lock: Towngas expert 5* accept (review id stamped explicitly via --expert-review-id) |
| 2026-05-24T13:22:15+00:00 | 0a2ba199-665f-41aa-a106-1163cad68d16-lookup-001 | `cbedcaa09002d2cd0f583360efe5430534809a8cf01c6bf38fd0e248ad76e645` | talon | 31ed6dec-1baf-4422-854d-4f4602f38eab | HARNESS-20 phase 2 retro-lock: Towngas expert 5* accept (review id stamped explicitly via --expert-review-id) |
| 2026-05-24T13:22:15+00:00 | 0a2ba199-665f-41aa-a106-1163cad68d16-lookup-002 | `9b534717dfed0f91b7a080859bc70adbf11f9ab32462002cc7cfa0fc80873b6f` | talon | b6c94941-6c75-4206-9dda-835a7981d57d | HARNESS-20 phase 2 retro-lock: Towngas expert 5* accept (review id stamped explicitly via --expert-review-id) |
| 2026-05-24T13:22:15+00:00 | 0a2ba199-665f-41aa-a106-1163cad68d16-lookup-003 | `e3784bda2588b9c095dfcbfbfd023e0e0874fa311792dd202e9ccc4df51656fe` | talon | c02ca834-4dc5-4f8b-812c-a692d91c0dd8 | HARNESS-20 phase 2 retro-lock: Towngas expert 5* accept (review id stamped explicitly via --expert-review-id) |
| 2026-05-24T13:22:15+00:00 | 0a2ba199-665f-41aa-a106-1163cad68d16-lookup-004 | `6d1c79d61910f18a075ef660e4ae2a93879db544fef434aee765b70cd2d1f1b9` | talon | f6b3e49c-56bb-44a5-8a1b-eb3648dfe436 | HARNESS-20 phase 2 retro-lock: Towngas expert 5* accept (review id stamped explicitly via --expert-review-id) |
| 2026-05-24T13:22:15+00:00 | 0a2ba199-665f-41aa-a106-1163cad68d16-lookup-005 | `841fddcd67b7f90b979a472451c34a101200868f0622a9fba3b00c81f1c8d5da` | talon | 7f24af01-c4c2-4499-9ea5-9d10fb7e78d7 | HARNESS-20 phase 2 retro-lock: Towngas expert 5* accept (review id stamped explicitly via --expert-review-id) |
| 2026-05-24T13:22:15+00:00 | 0a2ba199-665f-41aa-a106-1163cad68d16-lookup-006 | `3bf766885e8817a45f34a4bf38f04be725256604adae48f970f773a8f648a515` | talon | 496b0c37-8718-4b6b-b598-095ac817916d | HARNESS-20 phase 2 retro-lock: Towngas expert 5* accept (review id stamped explicitly via --expert-review-id) |
| 2026-05-24T13:22:15+00:00 | 0a2ba199-665f-41aa-a106-1163cad68d16-procedural-002 | `3b3b093a00db7234a1bdf31f490d5cfe880590b3ff412dd8db9481ef9923ff80` | talon | dd683f65-39fb-4fae-9bf1-f6f7d9b9c7b4 | HARNESS-20 phase 2 retro-lock: Towngas expert 5* accept (review id stamped explicitly via --expert-review-id) |
| 2026-05-24T13:22:15+00:00 | 0a2ba199-665f-41aa-a106-1163cad68d16-procedural-003 | `8e9e51fdc4af6c6027c36cee0dbd75bfd32df9b472e04f91e3d39be0e6647196` | talon | ed3affcb-8528-4620-b0f3-1b54e2097c0b | HARNESS-20 phase 2 retro-lock: Towngas expert 5* accept (review id stamped explicitly via --expert-review-id) |
| 2026-05-24T13:22:15+00:00 | 0a2ba199-665f-41aa-a106-1163cad68d16-procedural-004 | `ff6a4ed4854a36321c3f4973e85f1af8386db64226fd9fc276ba70527017c41f` | talon | 25af5812-c4fc-441c-af95-2ee499bd4437 | HARNESS-20 phase 2 retro-lock: Towngas expert 5* accept (review id stamped explicitly via --expert-review-id) |
| 2026-05-24T13:22:15+00:00 | 0a2ba199-665f-41aa-a106-1163cad68d16-procedural-005 | `952b969ce866d8d4dccca5fdb04d430eb538e452f1ae993485f29ce48da80c4f` | talon | b38c919f-9315-4a92-aba1-f817a9481714 | HARNESS-20 phase 2 retro-lock: Towngas expert 5* accept (review id stamped explicitly via --expert-review-id) |
| 2026-05-24T13:22:15+00:00 | 0a2ba199-665f-41aa-a106-1163cad68d16-procedural-006 | `ca8c0578b722e858e5c246f64173e730a47058230a9dbc11606295121b5f997d` | talon | a4e4c465-dd4d-4247-86b4-2a3004f1888e | HARNESS-20 phase 2 retro-lock: Towngas expert 5* accept (review id stamped explicitly via --expert-review-id) |
