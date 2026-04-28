# v2 Golden Set — Section Sampling Plan

**Manual:** MWS150-A (TRICITY155, Yamaha) — `90c229ff-de32-441e-8dea-11607859e00f`
**Source:** `tests/harness/evals/golden/v2/source/MWS150-A.md` (14,962 lines, 633 sections, 434 pages, zh-CN)
**Parser:** `app.harness_tools.manual_fs.parse_heading_tree` (production)
**Drafted:** 2026-04-25
**Status:** DRAFT — DTC bucket only. Symptom / Component / Image / Adversarial buckets pending.

---

## Why this file exists

This is the **planning gate** that v1 skipped. Each row below pre-commits one section we'll generate a golden test entry from. All 30 rows are picked by a human (with some heuristic help) **before** any LLM is invoked — protecting future evals from the v1 bias where random TOC sampling oversampled the back-of-book DTC index and undersampled real diagnostic procedures.

Once this file is signed off, every row drives one V4-Pro candidate-generation call, then one human review, then one frozen JSONL entry. No re-sampling, no improvising.

---

## Distribution target (30 entries)

| Bucket | Target | Drafted | Status |
|---|---|---|---|
| DTC procedures | 8 | 8 | DRAFT — awaiting review |
| Symptom flows | 6 | 0 | pending |
| Component specs | 6 | 0 | pending |
| Image / figure | 4 | 0 | pending |
| Adversarial | 6 | 0 | pending |
| **Total** | **30** | **8** | |

---

## DTC bucket (8/8 picked)

**Selection rules applied:**
- **Excluded** the back-of-book DTC index (`appendix-dtc-index`, lines 14937–14963) — the v1 oversample trap.
- **Excluded** stub headings under 200 chars (`p0107-p0108`, `p0122-p0123`, `18-46`, `77-85` were all empty/24-char placeholders left by the PDF converter).
- **Excluded** v1 entries (`p0112-p0113`, ABS code `53`) — picking different DTCs makes v1-vs-v2 comparison meaningful when we re-run the baseline.
- **Spanned** subsystems: engine sensor / engine ECU memory / ABS wheel-speed sensor / ABS solenoid / ABS internal logic / ABS continuous-operation actuator / ABS speed-pulse anomaly.
- **Mixed code namespaces:** 2 P-codes (engine ECM, OBD-II standard) + 6 numeric (ABS unit, manufacturer-specific) — reflects what a technician actually scans on this scooter.

**Slugs verified against the production `parse_heading_tree` parser** so they will match what the agent produces at eval time (this was a v1 failure mode — slugs in v1 didn't always match parser output, causing `section_match=0` even on correct answers).

| # | Slug | Section title | ~chars | Why chosen |
|---|---|---|---|---|
| dtc-001 | `p0117-p0118` | 故障代碼編號 P0117、P0118 — Engine coolant temperature sensor | 12,010 | Engine ECM, OBD-II standard P-code. Coolant temp ground-short / open-circuit pair — different subsystem from v1's intake-temp P0112. Procedure requires cold engine, multi-step continuity tests. |
| dtc-002 | `p062f` | 故障代碼編號 P062F — ECU EEPROM internal | 21,079 | Engine ECM, rare critical fault. Largest DTC section in the manual — explicitly tests whether the agent can summarise without dumping the whole section into context. |
| dtc-003 | `16` | 故障代碼編號 16 — ABS front wheel speed sensor | 4,309 | ABS unit, numeric code namespace. Wheel speed sensor (front) — high-frequency real-world fault. Tests agent recognition that "16" is a manufacturer DTC, not a P-code. |
| dtc-004 | `42-47` | 故障代碼編號 42、47 — ABS wheel speed signal anomaly | 4,066 | ABS unit, dual-DTC pairing in one procedure (42 OR 47 → same flow). Tests whether the agent reports both codes in `must_contain`, not just whichever the question asked about. |
| dtc-005 | `54` | 故障代碼編號 54 — ABS solenoid valve internal | 12,723 | ABS unit, hydraulic-actuator subsystem (vs sensors above). Largest ABS section. Explicit "do not separate hydraulic unit" warning — tests whether agent extracts safety notes. |
| dtc-006 | `33` | 故障代碼編號 33 — ABS internal / connector | 2,617 | ABS unit, internal-fault / connector subsystem. Tests "what to check first" reasoning when the manual's first instruction is "switch main switch OFF before disconnect". |
| dtc-007 | `78-86` | 故障代碼編號 78、86 — Wheel speed pulse blank | 8,207 | ABS unit, speed-conditional logic (>30 km/h → 78, <29 km/h → 86). Tests whether the agent surfaces the speed threshold — easy to miss if it skims. |
| dtc-008 | `28-73` | 故障代碼編號 28、73 — Left-front ABS persistent operation | 1,413 | ABS unit, duration-conditional logic (20s → 28, 36s → 73). Different actuator family from `42-47`. Smallest section in the bucket — tests whether thin sections can still anchor a useful entry. |

---

## What's NOT in this bucket (and why)

- **`14-27` and `29-74`** — same actuator-duration family as `28-73`. One representative is enough; including all three would test the same thing three times.
- **`24`** — wheel speed sensor, but `16` is a richer / more representative wheel-speed entry.
- **`31`, `71-25`, `72-25`, `75`** — internal/communication ABS faults. Picked `33` and `54` as bucket representatives; the remainder would over-weight ABS internal codes vs other subsystems.
- **`p0107-p0108`, `p0122-p0123`, `18-46`, `77-85`** — converter stubs (≤24 chars). No content to write a golden against.

---

## Open questions for reviewer

1. **Ratio of P-codes vs numeric:** drafted 2 P-codes / 6 numeric. The manual has only 3 P-code sections with real content (and v1 already used one), so 2 P-codes is the practical ceiling without revisiting the v1 entry. Acceptable, or do you want me to overlap with v1's P0112 to get a 3:5 split?
2. **Subsystem coverage:** drafted 1 sensor (engine), 1 ECU memory (engine), 2 wheel-speed sensors (ABS), 1 solenoid (ABS), 1 internal (ABS), 1 speed-pulse (ABS), 1 actuator (ABS). Any subsystem you want to drop or upweight?
3. **Are the smaller sections OK?** `dtc-008` (`28-73`) is only 1,413 chars. Big enough for a procedure but small enough that the golden answer might end up summarising 80% of the section. If you want a higher floor, I can swap it for one of the 4K+ ABS codes.

---

## Next bucket on deck

**Symptom flows** (6 entries) — symptom-troubleshooting chapter ("engine won't start", "ABS warning lit", etc.). Will surface 6 distinct symptom families, all with rich step-by-step diagnostic flowcharts. Pending DTC sign-off.
