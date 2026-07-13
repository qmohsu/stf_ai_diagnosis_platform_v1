"""Pydantic schemas for the comparative manual-eval suite.

Defines the contract between four layers used by the agent-vs-RAG
benchmark (HARNESS-15 / GitHub Issue #74):

- **Golden dataset** (``GoldenEntry``): human-reviewed question/answer
  pairs with citations to authoritative manual sections.  Stored as
  JSONL under ``golden/v2/``.  Carries a ``question_type`` axis so
  results can be sliced by retrieval-difficulty class (lookup,
  procedural, cross-section, image-required, adversarial).
- **System output** (``SystemRunResult``): unified shape produced by
  *both* systems under test (manual sub-agent and RAG retriever).
  The judge grades this without caring which system produced it.
- **Agent-specific output** (``ManualAgentResult``): the existing
  agent shape — re-exported from ``app.harness_agents.types`` so
  production code and the eval suite share one source of truth.
  An adapter maps it onto ``SystemRunResult`` for grading.
- **Judge output** (``Grade``): the LLM-as-judge's structured score,
  with continuous metrics in [0.0, 1.0] for benchmark-grade
  comparability (was binary in the v1 schema; widened so a system
  scoring 76.2 and one scoring 78.3 are distinguishable).

All models use ``BaseModel`` with explicit type annotations so
``model_dump()`` produces JSON-serializable payloads suitable for
both the golden JSONL files and the eval report artifact.

Author: Li-Ta Hsu
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field

# Re-export production shapes so existing imports under
# ``tests/harness/evals`` (and golden JSONL loaders) keep working
# while the definitions live next to the code that produces them.
from app.harness_agents.types import (  # noqa: F401
    Citation,
    DTCCitation,
    ManualAgentResult,
    OBDAgentResult,
    SectionRef,
    SignalCitation,
    StoppedReason,
    ToolCallTrace,
)


# ── Golden dataset models ─────────────────────────────────────────


class GoldenCitation(BaseModel):
    """Authoritative source anchor for a golden entry.

    Attributes:
        manual_id: The ingested manual's **stable UUID** — the
            ``manuals.id`` primary key, e.g.
            ``0a2ba199-665f-41aa-a106-1163cad68d16``.  Also the
            ``<manual_uuid>-`` prefix of ``GoldenEntry.id``.
            NEVER cover-code prose, filename stems, or marketing
            names (HARNESS-23 T11, #151): free-text identities
            drift — the first-round baseline broke because goldens
            said ``MWS-150-A`` while the corpus stored
            ``vehicle_model=TRICITY155``.  Prose aliases belong on
            the manual row itself (``manuals.factory_code``,
            APP-61), not in goldens.  Enforced at promotion time
            by ``scripts/promote_golden.py``.
        slug: Section slug as produced by
            ``manual_fs.parse_heading_tree``.
        quote: Short verbatim span from the section.  Used by the
            judge's grounding check — must appear in the cited
            manual section.
    """

    manual_id: str
    slug: str
    quote: str


GoldenCategory = Literal[
    "dtc", "symptom", "component", "adversarial", "image",
]
"""Domain category of the inquiry — secondary metadata for
sub-analysis (e.g., "how does each system do on DTC questions
vs component-spec questions?").  The PRIMARY slicing axis for
the agent-vs-RAG comparison is ``question_type`` below."""


GoldenQuestionType = Literal[
    # ── Manual-agent / RAG question types ─────────────────────────
    "lookup",            # single-fact retrieval; RAG might compete
    "procedural",        # multi-step diagnostic flow; agent should win
    "cross-section",     # combine info across ≥2 slugs; agent wins big
    "image-required",    # answer needs actual image bytes; RAG fails
    "adversarial",       # manual cannot answer; refusal expected
    # ── OBD-agent question types (HARNESS-21) ─────────────────────
    "signal_statistics", # mean/p95/max-style aggregate queries
    "event_finding",     # locate time windows matching a predicate
    "dtc_enumeration",   # list stored / pending DTCs
    "dtc_decode",        # decode an opaque code — honest pivot
                         # when no decoder is available
    "compound_obd",      # multi-tool narrative characterisation
    "adversarial_obd",   # OBD data cannot answer; refusal expected
]
"""Primary slicing axis for the comparative benchmark.  Independent
of ``GoldenCategory``: a DTC question can be ``lookup`` (just the
code's meaning), ``procedural`` (the diagnostic flow), or
``cross-section`` (which DTCs share a sensor).

The trailing six values are OBD-side variants — added in HARNESS-21
when the OBD sub-agent gained its own eval lane.  Membership in this
trailing set drives the lane dispatcher in ``metrics.py`` (an entry
whose ``question_type`` falls in the OBD set routes through
``metrics_obd.py`` rather than the manual rubric)."""


# Tuple form used by the lane dispatcher in ``metrics.py``.  Kept
# next to the literal definition so additions stay in sync.
OBD_QUESTION_TYPES: frozenset = frozenset({
    "signal_statistics",
    "event_finding",
    "dtc_enumeration",
    "dtc_decode",
    "compound_obd",
    "adversarial_obd",
})
"""Question-type values that route through the OBD eval lane."""


GoldenDifficulty = Literal["easy", "medium", "hard"]
"""Expected difficulty.  Currently informational only — useful for
slicing results post-hoc but not used by the rubric."""


# ── OBD-lane expectation models (HARNESS-21) ──────────────────────


class ExpectedSignalCitation(BaseModel):
    """Golden reference for one expected signal citation.

    Used by the OBD eval lane to express "the agent should cite signal
    X with statistic Y, value Z (±tolerance)" in a structured form.
    All fields beyond ``signal`` are optional — match only on what's
    specified, so an entry can require just the signal name (e.g.
    ``{"signal": "RPM"}``) or pin down the full ``(signal, stat,
    value)`` tuple.

    Attributes:
        signal: Signal/column name.  Matched case-insensitively
            against ``SignalCitation.signal``.
        stat: Optional statistic name (``"mean"``, ``"p95"``,
            ``"max"``, etc.).  When set, the cited stat must equal
            this value (case-sensitive — the OBD tools emit a
            canonical lowercase form).
        value: Optional reference value.  When set, the cited value
            must be within ``value_tolerance_rel`` of this number.
        value_tolerance_rel: Relative tolerance applied to ``value``.
            Default 5%, intended to absorb reasonable rounding while
            still catching fabricated numbers.  Per-citation override
            so dimensions like ``mean`` (noisy) can be looser than
            DTC count (must be exact).
        time_range: Optional ``(start_iso, end_iso)`` pair.  When
            set, the cited ``time_range`` must *overlap* this window
            — exact equality is too strict because the agent often
            quotes a slightly trimmed sub-window of the true event.
    """

    signal: str
    stat: Optional[str] = None
    value: Optional[float] = None
    value_tolerance_rel: float = 0.05
    time_range: Optional[Tuple[str, str]] = None


class ExpectedDTC(BaseModel):
    """Golden reference for one expected DTC citation.

    Attributes:
        code: DTC code string.  Matched case-insensitively against
            ``DTCCitation.code`` — Yamaha-hex codes can be authored
            in any case and still match agent output.
        status: Optional ``"stored"`` / ``"pending"``.  When set, the
            cited status must equal this value.  When ``None``, the
            DTC matches regardless of status (useful for "did the
            agent find this code at all" questions where the agent
            may report it under either status).
    """

    code: str
    status: Optional[Literal["stored", "pending"]] = None


class GoldenEntry(BaseModel):
    """Single human-reviewed question/answer pair.

    Immutable once frozen under ``golden/v2/``.  Corrections bump
    the version directory rather than editing in place, preventing
    silent drift of the eval set.

    The schema is deliberately system-agnostic — every field is
    populated against the source manual, not against the agent's
    or RAG's output.  Both systems are graded against the same
    entry using the same rubric.

    Attributes:
        id: Stable identifier, e.g.
            ``<manual_uuid>-dtc-001`` or
            ``<manual_uuid>-procedural-005``.
        category: Domain category (``dtc``, ``symptom``,
            ``component``, ``adversarial``, ``image``).  Secondary
            metadata.
        question_type: Primary axis for the comparative benchmark.
            One of ``lookup``, ``procedural``, ``cross-section``,
            ``image-required``, ``adversarial``.  Drives sub-suite
            reporting (e.g., "agent beats RAG on procedural by
            +46 points").
        difficulty: ``easy``, ``medium``, or ``hard``.
        question: Inquiry posed to the system under test.
            Phrased as a technician would type it.
        obd_context: Optional short snippet of OBD context that
            primes the agent (e.g. observed DTC list, symptom
            description).  None for pure manual-lookup tasks.
        golden_summary: Human-written reference answer, 3–8
            sentences.  Used by the judge's ``answer_quality``
            rubric dimension.
        golden_citations: Authoritative source locations that
            cover the answer.  Each cite's ``slug`` is the
            parser-canonical form (``manual_fs.parse_heading_tree``
            output), so it matches both the agent's
            ``Citation.slug`` and ``slugify(rag_chunk.section_title)``.
        expected_recall_slugs: Slugs that MUST appear in any
            complete retrieval set.  Often equal to the slugs in
            ``golden_citations``, but explicit so cross-section
            questions can require multiple slugs even if only one
            citation quote was extracted.
        expected_tool_trace: Loose guide for trajectory scoring.
            Agent-only — ignored when grading RAG.  Calibrated
            per-entry against actual agent runs (Step 5 of #74),
            not guessed.
        must_contain: Key facts that MUST appear in the system's
            ``output_text``.  Strings, case-insensitive substring
            match, whitespace-normalised across CJK boundaries.
        pitfall_directives: Natural-language "don't" instructions
            evaluated by the LLM judge (replaces the previous
            substring-based ``must_not_contain``).  Each directive
            is a sentence describing a specific failure mode the
            system MUST NOT exhibit.  Examples:
            ``"The output must not assert P0117 involves the
            oxygen sensor"`` or ``"The output must not present
            brake-system content as the answer"``.  The judge
            decides per directive whether the system violated it
            (semantic, context-aware — handles negation correctly:
            ``"this is NOT an O2 sensor issue"`` doesn't violate
            an ``"don't involve O2 sensor"`` directive).
            Directives are classified by phrasing
            (``judge_prompts.classify_pitfall_directive``, #147):
            ASSERTION directives ("must not assert/invent X")
            feed ``hallucination_penalty``; OMISSION directives
            ("must not omit X" / "must reference X") are judged
            and reported but excluded from the penalty — the
            omitted fact's ``must_contain`` entry already
            penalises it via ``fact_recall``.
        requires_image: Whether a complete answer requires the
            agent to surface actual image bytes (wiring diagram,
            exploded view, flowchart).  RAG fails ``image-required``
            entries by definition; the field is mainly used as a
            sanity flag during authoring.
        expected_signal_citations: OBD lane.  Golden reference for
            the agent's ``signal_citations``.  Empty for manual-lane
            entries.  Populated entries drive ``signal_recall``,
            ``signal_precision``, and ``value_accuracy`` in
            ``metrics_obd.py``.
        expected_dtcs: OBD lane.  Golden reference for the agent's
            ``dtc_citations``.  Empty for manual-lane entries.
            Drives ``dtc_accuracy``.
        expected_no_evidence: OBD lane.  When ``True``, flips the
            polarity of the OBD citation metrics: an empty
            ``signal_citations`` / ``dtc_citations`` from the agent
            scores 1.0 (compliant), non-empty scores 0.0.  Used for
            adversarial entries ("is the engine misfiring?" on a
            healthy log) and ``dtc_decode`` entries where the agent
            should pivot to "no decoder available" rather than
            fabricate a translation.  Always ``False`` for manual
            entries — the manual lane's no-evidence signal is
            ``question_type == "adversarial"``.  Both signals are
            consulted by ``judge_prompts._is_no_evidence_entry``
            so the judge credits a correct decline (#146).
        notes: Free-form reviewer comments.
    """

    id: str
    category: GoldenCategory
    question_type: GoldenQuestionType
    difficulty: GoldenDifficulty
    question: str
    obd_context: Optional[str] = None
    golden_summary: str
    # Default to an empty list so OBD-lane entries (which have no
    # manual-section citations) don't need to spell out the field.
    # Manual-lane authors continue to populate it explicitly.
    golden_citations: List[GoldenCitation] = Field(default_factory=list)
    expected_recall_slugs: List[str] = Field(default_factory=list)
    expected_tool_trace: List[str] = Field(default_factory=list)
    must_contain: List[str] = Field(default_factory=list)
    pitfall_directives: List[str] = Field(default_factory=list)
    requires_image: bool = False
    # ── OBD-lane optional fields (HARNESS-21) ─────────────────────
    expected_signal_citations: List[ExpectedSignalCitation] = Field(
        default_factory=list,
    )
    expected_dtcs: List[ExpectedDTC] = Field(default_factory=list)
    expected_no_evidence: bool = False
    notes: str = ""


# ── Agent output models ───────────────────────────────────────────
# ``Citation``, ``SectionRef``, ``ToolCallTrace``, ``ManualAgentResult``
# are re-exported from ``app.harness_agents.types`` at the top of
# this module.  Eval code should import them from here to keep the
# eval dependency surface flat.


# ── System-under-test output (unified across agent + RAG) ────────


SystemLabel = Literal["manual_agent", "rag", "obd_agent"]
"""Identifier for the system that produced a ``SystemRunResult``.

``"obd_agent"`` was added in HARNESS-21 when the OBD sub-agent gained
its own eval lane.  Routing to the OBD rubric in ``metrics.py``
is driven by ``GoldenEntry.question_type`` (see
``OBD_QUESTION_TYPES``), not by this label — the label is for
report-side identification only."""


class RetrievedChunkMetadata(BaseModel):
    """Per-chunk metadata for RAG retrievals.

    Captures information that's specific to vector retrieval
    (similarity score, chunk index, embedded metadata flags) so
    the eval report can show *why* a chunk was retrieved, not
    just *what* it contained.

    Attributes:
        chunk_index: Position of the chunk in the source
            document's chunk sequence.
        score: Cosine similarity (0–1, higher = more similar).
        section_title: Heading the chunk was extracted under
            (raw, before slugification).
        slug: ``slugify(section_title)`` — the canonical anchor
            that bridges to ``GoldenCitation.slug``.
        has_image: Whether the chunk contains a markdown image
            reference (Marker-generated description of a figure).
        dtc_codes: DTC codes auto-tagged on the chunk during
            ingestion (e.g. ``["P0117", "P0118"]``).
        text_preview: First ~120 chars of the chunk text, for
            human inspection of the eval report.
    """

    chunk_index: int
    score: float
    section_title: str
    slug: str
    has_image: bool = False
    dtc_codes: List[str] = Field(default_factory=list)
    text_preview: str = ""


class SystemRunResult(BaseModel):
    """Unified system-under-test output.

    Both the manual sub-agent and the RAG retriever normalise
    their results into this shape so the judge can grade them
    on the same rubric without caring which produced them.

    Attributes:
        system_label: Which system produced this result —
            ``"manual_agent"`` or ``"rag"``.
        question: The original inquiry, echoed for report-
            building convenience.
        output_text: The text the metric harness checks
            ``must_contain`` against (deterministic) and the
            judge evaluates ``pitfall_directives`` against
            (LLM-judged).  For the agent: the synthesised
            summary plus CITED-section text concatenated
            (cited only — exploration overhead is captured by
            ``exploration_cost``, not double-counted here).
            For RAG: the top-k retrieved chunks concatenated.
        claim_slugs: Slugs the system explicitly cited as
            answer sources.  For the agent: parser-canonical
            ``citations[].slug`` from the final JSON answer.
            For RAG: same as ``read_slugs`` — RAG has no
            synthesis step, so its retrieval IS its claim.
            Drives ``claim_precision``.
        read_slugs: Slugs the system actually accessed.  For
            the agent: parser-canonical ``raw_sections[].slug``
            from each ``read_manual_section`` call.  For RAG:
            same as ``claim_slugs``.  Drives
            ``exploration_cost`` (un-cited reads count as
            navigation overhead).
        retrieved_chunk_metadata: RAG-only.  Empty for the
            agent.  Captures per-chunk score / metadata so
            recall@k and precision@k can be reported.
        latency_ms_wall: End-to-end wall-clock time including
            network round-trips and provider queueing.  What
            real users experience.
        latency_ms_llm: Sum of LLM inference time (from
            OpenRouter ``usage`` records or local Ollama
            equivalents).  Reproducible across infra.  For
            RAG: the embedding-call time only.
        cost_usd: Total LLM cost.  For RAG: typically 0.0
            (Ollama embedding is free); positive only if the
            RAG track is wired to a paid embedder.
        tool_trace: Agent-only.  Empty for RAG.
        stopped_reason: Agent-only termination reason
            (``complete``, ``timeout``, ``max_iterations``,
            ``error``).  ``"complete"`` for RAG.
        iterations: Agent-only iteration count.  Always 1 for
            RAG.
        obd_signal_citations: OBD lane.  Signal/timestamp/value
            references the OBD sub-agent emitted in its final JSON.
            Populated by ``obd_runner._obd_result_to_system_run``;
            empty for ``manual_agent`` / ``rag`` runs.  Drives the
            ``signal_recall``, ``signal_precision``, and
            ``value_accuracy`` dimensions in ``metrics_obd.py``.
        obd_dtc_citations: OBD lane.  DTC references the OBD
            sub-agent emitted.  Drives ``dtc_accuracy``.  Empty for
            non-OBD systems.
    """

    system_label: SystemLabel
    question: str
    output_text: str
    claim_slugs: List[str] = Field(default_factory=list)
    read_slugs: List[str] = Field(default_factory=list)
    retrieved_chunk_metadata: List[RetrievedChunkMetadata] = Field(
        default_factory=list,
    )
    latency_ms_wall: float = 0.0
    latency_ms_llm: float = 0.0
    cost_usd: float = 0.0
    tool_trace: List[ToolCallTrace] = Field(default_factory=list)
    stopped_reason: str = "complete"
    iterations: int = 1
    # ── OBD-lane optional fields (HARNESS-21) ─────────────────────
    obd_signal_citations: List[SignalCitation] = Field(
        default_factory=list,
    )
    obd_dtc_citations: List[DTCCitation] = Field(default_factory=list)


# ── Judge output ──────────────────────────────────────────────────


class Grade(BaseModel):
    """Structured judge verdict for one ``SystemRunResult``.

    Continuous metrics in [0.0, 1.0] for benchmark-grade
    comparability.  The previous v1 schema used binary 0/1 for
    most dimensions, which collapsed cases like "matched 5/6
    expected slugs" into the same score as "matched 1/6" — both
    became ``section_match=1``.  This version preserves
    granularity so a system scoring 76.2 and one scoring 78.3
    are distinguishable.

    The substring-based metrics (``fact_recall``,
    ``hallucination_penalty``) are computed deterministically
    by the eval harness, NOT by the LLM judge — they're
    reproducible across runs.  The judge fills in
    ``answer_quality`` (LLM-judged holistic rating) and
    ``reasoning`` only.

    Attributes:
        section_recall: Fraction of ``expected_recall_slugs`` the
            system surfaced anywhere (``claim_slugs ∪ read_slugs``).
            Slugs are matched slug-tolerantly (HARNESS-23 T4, #145),
            not by exact string: an expected slug counts as surfaced
            when its normalised form matches a surfaced slug OR a
            golden citation's verbatim ``quote`` appears in the
            surfaced text.  This stops correct answers scoring 0
            when a Chinese-derived heading slugifies differently by
            navigation path.  Higher = better.  ``None`` = N/A
            (#148): the golden has empty ``expected_recall_slugs``
            (manual adversarial — nothing SHOULD be retrieved), so
            the dimension is undefined and excluded from
            ``overall`` (its weight is renormalised over the
            remaining dims in ``metrics.compute_overall``).
            Previously scored as a vacuous 1.0, which inflated
            adversarial ``overall`` by a free +0.20 in both lanes.
        claim_precision: Of the slugs the system **explicitly
            cited as answer sources**, what fraction were
            correct — matched slug-tolerantly (HARNESS-24,
            #192), not by exact intersection: a claimed slug
            counts when its normalised form matches an expected
            slug OR a golden citation quote appears in the
            claimed deliverable text (the manual states many
            facts in multiple sections; citing the sibling
            section the golden didn't pick is not fabrication).
            Quote-containment credit is capped at ``|claim|``,
            so junk citations alongside a right one still cost.
            Replaces the older ``section_precision`` which
            conflated reads with citations and unfairly
            penalised the agent for legitimate index-flipping
            navigation.  RAG: same semantics (claim ==
            retrieval).  Higher = better.  ``None`` = N/A
            (#192): empty ``expected_recall_slugs`` (manual
            adversarial) leaves precision with no positive
            class, so the dim is undefined and excluded from
            ``overall`` with weight renormalisation — the old
            polarity (silent 1.0 / any citation 0.0) scored
            corrective citations (declines citing the section
            that refutes the premise) as fabrication.
            Adversarial citation polarity remains graded by
            ``citation_quality``.
        exploration_cost: ``1 - |claim_slugs ∩ read_slugs| /
            max(|read_slugs|, 1)``.  Fraction of read sections
            that were NOT cited in the final answer.  Captures
            "how much navigation overhead did the agent pay?"
            For RAG (no synthesis step) this is always 0.0
            — RAG's reads ARE its claims.  LOWER = better.
            Reported but NOT in ``overall`` since the
            2026-07-12 structural-floor rebalance (HARNESS-23
            T7, #153): like ``trajectory_efficiency`` it's an
            agent-only efficiency/trade-off dim, and weighting
            it in a shared cross-lane formula only handed RAG
            free credit via the ``(1 - cost)`` term.
        fact_recall: Fraction of ``must_contain`` items found
            in ``output_text`` (case-insensitive,
            whitespace-normalised substring).
        fact_density: ``fact_hits / max(output_words / 100, 1)``.
            Rewards concise answers that hit all the facts.
            A 50-word answer with 5 facts beats a 500-word
            answer with the same 5 facts.
        hallucination_penalty: LLM-judged.  The judge receives the
            entry's ``pitfall_directives`` plus the system output,
            and decides per-directive whether the output violates
            it (semantic, context-aware — distinguishes assertion
            from negation).  Only ASSERTION-type directives
            ("must not say/assert/invent X") count toward the
            penalty (#147); omission-type directives ("must not
            omit X" / "must reference X") are judged and reported
            but excluded — omission is a recall failure already
            measured by ``fact_recall``.  Score = ``max(0.1,
            1 - 0.3 * violation_count)``: 0 violations = 1.0,
            1 = 0.7, 2 = 0.4, 3+ = 0.1.  Soft curve gives partial
            credit for "almost right" cases.  HIGHER = better.
        citation_quality: Tiered, computed against
            ``claim_slugs`` with the same slug-tolerant matching
            as ``section_recall`` (#145).
            0.0 = no claim slugs (system cited nothing).
            0.3 = claimed slugs, but none cover an
                  ``expected_recall_slugs`` section (cited but
                  wrong).
            1.0 = ≥1 claimed section covers a golden slug (by
                  normalised slug OR golden-quote containment).
        answer_quality: LLM-judged 0.0–1.0 rating of the
            answer's correctness, completeness, and clarity
            against ``golden_summary``.  RAGAs / G-Eval style.
            The only non-deterministic metric.  On no-evidence /
            false-premise entries (``question_type`` adversarial
            in either lane, or ``expected_no_evidence=True``) the
            judge is told the correct response is a decline and
            rates the QUALITY OF THE DECLINE (#146): an explicit,
            correct refusal that names the premise error scores
            high; fabricating an answer scores ≤ 0.2.
        trajectory_efficiency: Agent-only.
            ``min(1.0, expected_calls / max(actual_calls,
            expected_calls))`` with brute-force-detection guard.
            Reported but NOT in ``overall`` — it's a trade-off
            dim, not a quality dim.  RAG always scores 1.0.
        value_accuracy: OBD lane.  Fraction of comparable
            (signal, stat, value) pairs within tolerance.
            Neutral 1.0 for manual entries — they have no
            numerical-value semantics, so the dimension shouldn't
            penalise them.  Computed deterministically in
            ``metrics_obd.compute_value_accuracy``.  Weight
            halved 0.10 → 0.05 in the 2026-07-12 structural-
            floor rebalance (#153): the neutral 1.0 is pure
            free credit on manual-lane entries, so its weight
            is kept to the minimum that still makes fabricated
            OBD numbers cost.
        overall: Weighted ``[0.0, 1.0]``.  Current formula (post
            HARNESS-23 T7 structural-floor rebalance, #153):
            0.25*section_recall
            + 0.10*claim_precision
            + 0.00*(1 - exploration_cost)  [reported-only]
            + 0.20*fact_recall
            + 0.05*fact_density
            + 0.15*hallucination_penalty
            + 0.05*citation_quality
            + 0.05*value_accuracy
            + 0.15*answer_quality.
            Reported in eval reports as percentage (× 100).
            When ``section_recall`` or ``claim_precision`` is
            ``None`` (N/A — #148, #192) the term is dropped and
            the remaining weights are renormalised (× 1/0.75
            for section_recall alone, × 1/0.65 when both are
            N/A, under default weights; the two co-occur on
            manual adversarial entries).
            Weights still tunable — see
            ``DEFAULT_OVERALL_WEIGHTS`` in metrics.py (its
            comment block carries the dated rebalance history,
            including why a zero-content answer still floors at
            ~0.30: vacuous claim_precision + saturated
            hallucination_penalty + neutral value_accuracy).
        reasoning: 2–4 sentences from the judge citing specific
            evidence for ``answer_quality``.  Substring metrics
            don't need reasoning (they're deterministic).
    """

    # ``None`` = N/A (empty ``expected_recall_slugs``; excluded
    # from ``overall`` — see #148 and the attribute docstring).
    section_recall: Optional[float] = Field(
        default=None, ge=0.0, le=1.0,
    )
    # ``None`` = N/A (empty ``expected_recall_slugs``; excluded
    # from ``overall`` — see #192 and the attribute docstring).
    claim_precision: Optional[float] = Field(
        default=None, ge=0.0, le=1.0,
    )
    exploration_cost: float = Field(ge=0.0, le=1.0)
    fact_recall: float = Field(ge=0.0, le=1.0)
    fact_density: float = Field(ge=0.0, le=1.0)
    hallucination_penalty: float = Field(ge=0.0, le=1.0)
    citation_quality: float = Field(ge=0.0, le=1.0)
    answer_quality: float = Field(ge=0.0, le=1.0)
    trajectory_efficiency: float = Field(ge=0.0, le=1.0, default=1.0)
    # HARNESS-21 addition.  Default 1.0 so old report JSON
    # (pre-HARNESS-21) still loads cleanly.
    value_accuracy: float = Field(ge=0.0, le=1.0, default=1.0)
    overall: float = Field(ge=0.0, le=1.0)
    reasoning: str


# ── Helper: deterministic metric extras ───────────────────────────
# These are computed by the eval harness, not the judge, but live
# alongside ``Grade`` in eval reports.


class TradeoffMetrics(BaseModel):
    """Latency + cost trade-off measurements per system run.

    Reported alongside ``Grade`` in eval reports.  NOT included
    in ``overall`` because mixing quality with latency/cost
    requires a domain-specific exchange rate ($/quality-point)
    that the eval harness shouldn't presume.

    Attributes:
        latency_ms_wall: Wall-clock end-to-end.
        latency_ms_llm: LLM inference time only.
        cost_usd: Dollar cost of LLM calls.
    """

    latency_ms_wall: float = 0.0
    latency_ms_llm: float = 0.0
    cost_usd: float = 0.0
