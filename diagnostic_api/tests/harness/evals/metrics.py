"""Deterministic metric computation for the comparative eval suite.

The judge model is responsible only for the subjective
``answer_quality`` rating; everything else in the rubric is
computed here from the ``GoldenEntry`` and ``SystemRunResult``
without LLM involvement.  This split keeps the bulk of the
score reproducible across runs and avoids paying for judge
calls on metrics that don't need them.

Inputs:
- ``GoldenEntry``  — the authoritative reference for one
  question.
- ``SystemRunResult`` — what one system (agent or RAG) produced
  for that question.

Output:
- ``DeterministicMetrics`` — a typed dict of rubric dimensions
  that the judge later combines with its own
  ``answer_quality`` rating into a final ``Grade``.

Whitespace normalisation matches the conventions established in
``scripts.generate_golden_candidates`` (collapse CJK gaps so a
line-wrapped Chinese phrase still substring-matches its
unwrapped counterpart).  ``fact_recall`` additionally matches
``must_contain`` terms bilingual-tolerantly — see the
"Bilingual fact matching" section (HARNESS-23 T9, #149).

Author: Li-Ta Hsu
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import List, Optional, Set

from tests.harness.evals.metrics_obd import (
    compute_obd_deterministic_metrics,
)
from tests.harness.evals.schemas import (
    OBD_QUESTION_TYPES,
    GoldenCitation,
    GoldenEntry,
    SystemRunResult,
)


# ── Tokenizer (lazy-init) ────────────────────────────────────────


# cl100k_base is the GPT-4 / DeepSeek-family BPE tokenizer.  We use
# it to count tokens in fact_density's conciseness factor — tokens
# are the right cost unit because the downstream consumer of the
# manual agent's output is another diagnose LLM, where context
# tokens are the actual budget.  Word-based counting (.split()) is
# biased against languages without inter-word whitespace (Chinese,
# Japanese), which matters for our bilingual manual.
_ENC = None


def _count_tokens(text: str) -> int:
    """Return the cl100k_base token count for ``text``.

    Lazily imports and caches the tiktoken encoding singleton
    (the encoding object is small but the import is non-trivial).
    Falls back to a coarse ``len(text) // 4`` estimate if tiktoken
    isn't available — mirrors the OpenAI rule-of-thumb and keeps
    metrics computable in environments without the optional dep.

    Args:
        text: Output text from the system under evaluation.

    Returns:
        Token count, or 0 for empty/None input.
    """
    global _ENC
    if not text:
        return 0
    if _ENC is None:
        try:
            import tiktoken
            _ENC = tiktoken.get_encoding("cl100k_base")
        except Exception:
            # Sentinel: don't retry the import on every call.
            _ENC = False
    if _ENC is False:
        return max(1, len(text) // 4)
    return len(_ENC.encode(text))


# ── Whitespace normalisation (mirrors generator script) ──────────


_CJK_CLASS = r"[一-鿿　-〿＀-￯]"
"""CJK ideographs + symbols/punctuation + fullwidth forms.
A run of whitespace between two CJK characters almost always
came from a line break in the source PDF; collapse it."""


_CJK_WS_CJK_RE = re.compile(rf"({_CJK_CLASS})\s+(?={_CJK_CLASS})")
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_ws(text: str) -> str:
    """Collapse CJK gaps then any remaining whitespace runs.

    See ``scripts.generate_golden_candidates._normalize_ws`` —
    same logic, lifted here so the eval doesn't depend on a
    script under ``scripts/``.

    Args:
        text: Raw string.

    Returns:
        Normalised copy suitable for substring comparison.
    """
    prev: Optional[str] = None
    while prev != text:
        prev = text
        text = _CJK_WS_CJK_RE.sub(r"\1", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


# ── Slug-tolerant matching (HARNESS-23 T4, #145) ─────────────────
#
# The manual is bilingual and its section headings slugify from
# Chinese text.  The SAME section can slugify DIFFERENTLY depending
# on the navigation path it was reached by (separator style,
# full/half-width forms, a parent-heading prefix, surrounding
# whitespace).  Exact set-intersection on the raw slug string
# therefore scores correct answers as 0 on ``section_recall`` /
# ``citation_quality`` purely because the label is spelled
# differently — even when the content is right (see ``lookup-002``
# in the phase-6 baseline: fact_recall=1.0 but citation_quality=0.3,
# section_recall=0.0).
#
# The fix is two-tier, deterministic (no LLM):
#   1. Normalised slug equality — collapse the cosmetic drift.
#   2. Golden-quote containment — if the system's surfaced text
#      contains a golden citation's verbatim ``quote``, the section
#      was genuinely surfaced regardless of how its slug rendered.


_SLUG_SEP_RE = re.compile(r"[\W_]+", re.UNICODE)
"""Separators/punctuation/whitespace/underscore.  Stripped for
tolerant slug equality; CJK letters and alphanumerics (which are
word characters under ``re.UNICODE``) are preserved, so genuine
distinctions like a trailing ``-2`` disambiguator survive."""


def _normalize_slug(slug: str) -> str:
    """Canonicalise a section slug for tolerant equality.

    Collapses the cosmetic differences that make the *same* manual
    section slugify differently by navigation path: NFKC-folds
    full/half-width forms, case-folds any Latin, and strips
    separators/punctuation.  Deterministic — identical input always
    yields identical output.

    Args:
        slug: Raw section slug from a golden or a system run.

    Returns:
        Normalised slug, or ``""`` for empty/None input.
    """
    if not slug:
        return ""
    norm = unicodedata.normalize("NFKC", slug).casefold()
    return _SLUG_SEP_RE.sub("", norm)


def _quote_in_text(quote: str, text: str) -> bool:
    """True when ``quote`` appears in ``text`` (ws-normalised, ci).

    Reuses ``_normalize_ws`` so a line-wrapped Chinese quote still
    substring-matches its unwrapped counterpart — the same
    convention ``_compute_fact_recall`` uses for ``must_contain``.

    Args:
        quote: Verbatim span from a ``GoldenCitation``.
        text: Text the system surfaced (deliverable / previews).

    Returns:
        Whether the normalised quote is a substring of the
        normalised text.  Empty quote or text returns ``False``.
    """
    if not quote or not text:
        return False
    norm_quote = _normalize_ws(quote).lower()
    if not norm_quote:
        return False
    return norm_quote in _normalize_ws(text).lower()


def _covered_expected_slugs(
    expected: List[str],
    golden_citations: List[GoldenCitation],
    surfaced_slugs: List[str],
    surfaced_text: str,
) -> Set[str]:
    """Expected slugs the system covered, matched slug-tolerantly.

    An expected slug counts as covered when EITHER holds:

    - **(a) Normalised slug match** — its ``_normalize_slug`` form
      equals that of any slug the system surfaced.  Absorbs
      navigation-path spelling drift.
    - **(b) Golden-quote containment** — a golden citation *for that
      slug* has its verbatim ``quote`` present in ``surfaced_text``.
      The system quoted the right section even though its slug label
      differs.

    Golden citations are keyed by their own normalised slug, so a
    quote only rescues the expected slug it actually documents;
    expected slugs with no golden quote (e.g. extra cross-section
    slugs) can still be covered via path (a).

    Args:
        expected: ``GoldenEntry.expected_recall_slugs``.
        golden_citations: ``GoldenEntry.golden_citations`` (slug +
            verbatim quote).
        surfaced_slugs: Slugs the system surfaced in the relevant
            channel (claim ∪ read for recall; claim for citation).
        surfaced_text: Text the system surfaced in that channel.

    Returns:
        The subset of ``expected`` deemed covered.  Empty when
        ``expected`` is empty (adversarial entries surface nothing
        by design — recall becomes N/A (#148); precision/citation
        polarity is handled by the callers).
    """
    norm_surfaced = {_normalize_slug(s) for s in surfaced_slugs if s}
    quotes_by_slug: dict = {}
    for cite in golden_citations:
        quotes_by_slug.setdefault(
            _normalize_slug(cite.slug), [],
        ).append(cite.quote)
    covered: Set[str] = set()
    for slug in expected:
        if not slug:
            continue
        norm = _normalize_slug(slug)
        if norm in norm_surfaced:
            covered.add(slug)
            continue
        if any(
            _quote_in_text(q, surfaced_text)
            for q in quotes_by_slug.get(norm, [])
        ):
            covered.add(slug)
    return covered


# ── Bilingual fact matching (HARNESS-23 T9, #149) ────────────────
#
# The corpus is bilingual (Traditional Chinese + English) but most
# golden ``must_contain`` items are exact CJK substrings.  A correct
# answer phrased in English (or with cosmetic spacing drift like
# ``4行程`` vs ``4 行程``) failed the literal substring scan and
# zeroed ``fact_recall`` — e.g. ``cross-005`` scored
# ``fact_recall=0`` despite the agent finding the right section,
# because its ``must_contain`` is exact CJK tokens (``右前``,
# ``左前``, ``後煞車卡鉗``).
#
# The fix is metrics-side (no golden re-promotion) and fully
# deterministic — two ADDITIVE tiers on top of the original exact
# substring scan (tier 1, unchanged, always checked first):
#
#   2. **Flexible-whitespace CJK match** — for terms containing a
#      CJK character, whitespace runs in the term become optional
#      (``4 行程`` matches ``4行程``; ``綠色 / 紅色`` matches
#      ``綠色/紅色``).  A leading Latin/digit boundary guard stops
#      mid-word joins (``V 皮帶`` must NOT match inside ``CVT皮帶``).
#   3. **Translation-equivalent match** — a small curated map of
#      the recurring bilingual spec terms in the locked goldens.
#      A golden term whose normalised form has an entry also hits
#      when any listed equivalent phrase appears in the output
#      (``右前`` ⇒ ``front right``; ``TDC`` ⇐ ``上死點``).
#      Equivalents are matched with the same flexible-whitespace
#      pattern + leading boundary guard.  No trailing guard — a
#      plural/suffix form (``cold tires``) still credits its
#      singular equivalent; under-counting correct answers is the
#      bug being fixed, so mild morphological looseness is the
#      accepted trade-off.


_CJK_CHAR_RE = re.compile(_CJK_CLASS)
_LATIN_ALNUM_RE = re.compile(r"[a-z0-9]")


# Curated for the recurring spec terms in
# ``golden/v2/locked/mws150a.jsonl``.  Keys are golden
# ``must_contain`` terms (any cosmetic spelling — they are keyed
# through ``_normalize_slug`` at load time); values are accepted
# equivalent phrases in the other language.  Keep entries
# reviewable: one concept per key, only phrasings the manual /
# a competent answer would actually use.
_MUST_CONTAIN_EQUIVALENTS: dict = {
    # Positions / brakes (cross-005, procedural-005, lookup-006).
    "右前": ("front right", "right front"),
    "左前": ("front left", "left front"),
    "後煞車卡鉗": ("rear brake caliper",),
    "煞車卡鉗": ("brake caliper",),
    "冷胎": ("cold tire", "cold tyre", "tires are cold",
             "tyres are cold"),
    # Engine / valve train.
    "汽門間隙": ("valve clearance",),
    "汽門": ("valve",),
    "冷機": ("cold engine", "engine cold", "engine is cold"),
    "引擎冷機": ("cold engine", "engine cold", "engine is cold"),
    "暖車": ("warm up", "warm-up", "warmed up"),
    "進氣": ("intake",),
    "活塞環": ("piston ring",),
    "上死點": ("top dead center", "top dead centre"),
    "凸輪軸鏈輪": ("camshaft sprocket",),
    "曲軸箱": ("crankcase",),
    "對準記號": ("alignment mark", "match mark", "matching mark"),
    "平衡器": ("balancer",),
    "4 行程": ("4-stroke", "four-stroke", "four stroke"),
    "惰轉": ("idle", "idling"),
    # Cooling system.
    "恆溫器": ("thermostat",),
    "水箱蓋": ("radiator cap",),
    "空氣釋放螺栓": ("air bleed bolt", "air bleed screw"),
    "冷卻液溫度感知器": ("coolant temperature sensor",),
    # Sensors / electrical / DTC symptom text.
    "曲軸位置感知器": ("crankshaft position sensor",),
    "進氣壓力感知器": ("intake air pressure sensor",
                        "intake pressure sensor"),
    "搭鐵短路": ("short to ground", "short circuit to ground",
                 "shorted to ground"),
    "電氣式": ("electric", "electrical"),
    "引擎無法起動": ("engine does not start",
                     "engine will not start",
                     "engine won't start",
                     "engine cannot be started",
                     "engine fails to start"),
    "無法運轉": ("does not run", "cannot run", "will not run",
                 "won't run", "fails to run"),
    # Wire colours.
    "綠色 / 紅色": ("green / red", "green-red"),
    "粉紅色 / 綠色": ("pink / green", "pink-green"),
    "藍色": ("blue",),
    # Transmission (adversarial).
    "V 皮帶": ("v-belt", "v belt"),
    "自動": ("automatic",),
    # Cables / marks / fluids.
    "油門鋼索": ("throttle cable",),
    "加速鋼索": ("accelerator cable",),
    "減速鋼索": ("decelerator cable", "deceleration cable"),
    "鋼索配置": ("cable routing",),
    "最低油位記號": ("minimum level mark", "minimum oil level mark",
                     "low level mark"),
    "最高油位記號": ("maximum level mark", "maximum oil level mark",
                     "high level mark"),
    "量油尺": ("dipstick", "oil level gauge"),
    "機油更換指示器": ("oil change indicator",),
    "耐熱橡皮管": ("heat-resistant rubber hose",
                   "heat resistant rubber hose"),
    "橡皮套": ("rubber cover", "rubber boot", "rubber damper"),
    "圖示位置": ("position shown", "as shown in the figure",
                 "shown in the illustration"),
    # Reverse direction (EN golden term ⇐ CJK answer) for the
    # English items already in the locked goldens.
    "TDC": ("上死點",),
    "no chain": ("無鏈條", "沒有鏈條", "非鏈條"),
    "no manual transmission": ("無手排", "沒有手排", "非手排"),
    "no turbocharger": ("無渦輪", "沒有渦輪", "非渦輪"),
    "no carburetor": ("無化油器", "沒有化油器", "非化油器"),
    "no premix": ("無需預混", "不需預混", "無須預混"),
    "not defined": ("未定義", "沒有定義", "無此故障碼"),
    "naturally aspirated": ("自然進氣",),
    "4-stroke": ("4 行程", "四行程"),
}


def _term_pattern(norm_term: str) -> "re.Pattern[str]":
    """Compile a flexible-whitespace pattern for ``norm_term``.

    ``norm_term`` must already be ``_normalize_ws``-ed and
    lowercased.  Whitespace runs in the term become ``\\s*`` so
    spacing drift on either side matches (``4 行程`` vs ``4行程``,
    ``綠色 / 紅色`` vs ``綠色/紅色``).  When the term starts with a
    Latin letter or digit, a negative lookbehind stops mid-word
    joins (``v 皮帶`` must not match inside ``cvt皮帶``; ``4 行程``
    must not match inside ``14行程``).  No trailing guard — see the
    section comment for the rationale.

    Args:
        norm_term: Normalised, lowercased term or equivalent.

    Returns:
        Compiled pattern for ``re.Pattern.search`` over
        ``_normalize_ws``-ed lowercased output text.
    """
    parts = [re.escape(p) for p in norm_term.split(" ") if p]
    body = r"\s*".join(parts)
    if _LATIN_ALNUM_RE.match(norm_term):
        body = r"(?<![a-z0-9])" + body
    return re.compile(body)


def _normalize_equiv(text: str) -> str:
    """Normalise a term/equivalent for pattern building."""
    return _normalize_ws(text).lower()


# Normalised-key lookup, built once at import.  Keyed through
# ``_normalize_slug`` so a golden term matches its map entry
# regardless of cosmetic spacing / width / case drift.
_EQUIVALENT_PATTERNS: dict = {
    _normalize_slug(term): tuple(
        _term_pattern(_normalize_equiv(eq)) for eq in equivalents
    )
    for term, equivalents in _MUST_CONTAIN_EQUIVALENTS.items()
}


def _fact_term_in_text(term: str, norm_text: str) -> bool:
    """Three-tier deterministic scan for one ``must_contain`` term.

    Tiers (first hit wins):

    1. Exact substring — ``_normalize_ws``-ed, case-insensitive
       (original behaviour, unchanged).
    2. Flexible-whitespace match — CJK-containing terms only.
    3. Translation equivalents — via ``_MUST_CONTAIN_EQUIVALENTS``.

    Args:
        term: One golden ``must_contain`` item (verbatim).
        norm_text: Output text, already ``_normalize_ws``-ed and
            lowercased by the caller.

    Returns:
        Whether the term (or an accepted equivalent) is present.
    """
    norm_term = _normalize_equiv(term or "")
    if not norm_term:
        return False
    if norm_term in norm_text:
        return True
    if _CJK_CHAR_RE.search(norm_term) and _term_pattern(
        norm_term,
    ).search(norm_text):
        return True
    return any(
        pattern.search(norm_text)
        for pattern in _EQUIVALENT_PATTERNS.get(
            _normalize_slug(norm_term), (),
        )
    )


# ── Output container ─────────────────────────────────────────────


@dataclass(frozen=True)
class DeterministicMetrics:
    """Rubric dimensions that don't need an LLM judge.

    Computed by ``compute_deterministic_metrics``; combined with
    ``answer_quality`` and ``hallucination_penalty`` from the
    judge to form the final ``Grade``.

    Attributes:
        section_recall: Fraction of golden slugs the system
            surfaced anywhere (claim ∪ read), matched
            slug-tolerantly (normalised slug OR golden-quote
            containment) so a correct section spelled with a
            different slug still counts (#145).  ``None`` = N/A:
            the golden has no ``expected_recall_slugs`` (manual
            adversarial — nothing SHOULD be retrieved), so the
            dimension is excluded from ``compute_overall`` with
            its weight renormalised over the remaining dims
            instead of vacuously scoring 1.0 (#148).
        claim_precision: Fraction of CITED slugs that match
            golden.  Replaces the older ``section_precision``
            which conflated reads and citations.
        exploration_cost: Fraction of READ slugs that were
            NOT cited.  Higher = more navigation waste.  Always
            0.0 for RAG (no synthesis step).
        fact_recall: Fraction of must_contain present in output,
            matched bilingual-tolerantly (exact substring OR
            flexible-whitespace CJK OR curated EN/CJK
            translation equivalent) so a correct English answer
            to a CJK-exact golden still counts (#149).
        fact_density: Fact hits × conciseness factor.
        citation_quality: Tiered (0.0 / 0.3 / 1.0), against
            claim_slugs, matched slug-tolerantly (#145).
        trajectory_efficiency: Agent-only; 1.0 for RAG.
        fact_recall_hits: Concrete list of must_contain that hit
            (for reasoning / debugging).
        fact_recall_misses: Concrete list of must_contain that
            didn't hit.

    Note:
        ``hallucination_penalty`` USED to live here as a
        deterministic substring-based metric (``1 - count *
        0.5`` against ``must_not_contain``).  As of v2.12 it's
        LLM-judged from ``pitfall_directives`` and computed in
        ``judge.grade_run`` instead.  See
        ``compute_hallucination_penalty`` below.
    """

    section_recall: Optional[float]
    claim_precision: float
    exploration_cost: float
    fact_recall: float
    fact_density: float
    citation_quality: float
    trajectory_efficiency: float
    # OBD-lane addition (HARNESS-21).  Manual-lane entries leave
    # this at the neutral 1.0 so the rebalanced
    # ``DEFAULT_OVERALL_WEIGHTS`` doesn't penalise them for the
    # absence of numerical citations.  See
    # ``metrics_obd.compute_value_accuracy``.
    value_accuracy: float = 1.0
    # Diagnostics — not metrics themselves, but useful for
    # report-generation and judge prompting.
    fact_recall_hits: List[str] = field(default_factory=list)
    fact_recall_misses: List[str] = field(default_factory=list)


# ── Per-metric helpers ───────────────────────────────────────────


def _compute_section_recall(
    expected: List[str], covered: Set[str],
) -> Optional[float]:
    """``|covered| / |expected|``, ``None`` (N/A) when expected empty.

    ``covered`` is the slug-tolerant subset of ``expected`` the
    system surfaced, resolved by ``_covered_expected_slugs``
    (normalised slug match OR golden-quote containment over the
    union of ``claim_slugs`` and ``read_slugs``).  This replaces the
    exact-string ``surfaced ∩ expected`` intersection, which
    under-counted correct answers whose Chinese-derived slug
    rendered differently from the golden (#145).

    Empty ``expected`` (manual adversarial entries — no slug is
    the right answer, so there is nothing to recall) returns
    ``None``, meaning N/A: the dimension is undefined for the
    entry and ``compute_overall`` excludes it, renormalising its
    weight over the remaining dims.  Previously this returned a
    vacuous 1.0, which inflated adversarial overall by ~+0.20 of
    free floor for both lanes (#148).
    """
    expected_set = {s for s in expected if s}
    if not expected_set:
        return None
    covered_set = {s for s in covered if s} & expected_set
    return len(covered_set) / len(expected_set)


def _compute_claim_precision(
    expected: List[str], claim: List[str],
) -> float:
    """``|claim ∩ expected| / |claim|``, 1 when claim empty.

    Replaces the older ``_compute_section_precision`` which
    operated over the union of claim + read.  This version
    only counts what the system **explicitly cited as an
    answer source** — for the agent, slugs from
    ``result.citations[].slug``; for RAG, slugs from each
    retrieved chunk (RAG has no separate synthesis step).

    Empty ``claim`` returns 1.0 (vacuously precise).  But a
    system that claimed nothing also scores 0 on
    ``section_recall``, so it can't ride this freebie to a
    high overall score.

    Adversarial entries (empty ``expected``) are a special
    case: if the system claimed anything, it's all "wrong"
    — precision = 0.  If the system correctly stayed silent,
    precision = 1.
    """
    if not claim:
        return 1.0
    if not expected:
        # Adversarial: anything claimed is incorrect.
        return 0.0
    expected_set = {s for s in expected if s}
    claim_set = {s for s in claim if s}
    if not claim_set:
        return 1.0
    overlap = expected_set & claim_set
    return len(overlap) / len(claim_set)


def _compute_exploration_cost(
    read: List[str], claim: List[str],
) -> float:
    """``1 - |claim ∩ read| / max(|read|, 1)``.  LOWER is better.

    Captures "how much navigation overhead did the agent pay?"
    Of the slugs the agent actually accessed via
    ``read_manual_section``, what fraction did NOT make it
    into the final claim?  A perfectly efficient agent reads
    only what it cites (cost = 0.0); an agent that reads many
    sections and cites few pays a higher exploration cost.

    For RAG: ``read_slugs == claim_slugs`` (no synthesis),
    so cost is always 0.0.  This is intentional — RAG doesn't
    pay an exploration cost because there's no separate
    "navigation vs grounding" distinction in its workflow.

    Args:
        read: Slugs the system accessed.
        claim: Slugs the system explicitly cited.

    Returns:
        Cost in [0.0, 1.0].
    """
    read_set = {s for s in read if s}
    if not read_set:
        return 0.0  # No reads → no waste.
    claim_set = {s for s in claim if s}
    cited_count = len(read_set & claim_set)
    return 1.0 - (cited_count / len(read_set))


def _compute_fact_recall(
    must_contain: List[str], output_text: str,
) -> tuple[float, List[str], List[str]]:
    """Bilingual-tolerant scan of ``must_contain`` over output.

    Each term hits via the three-tier deterministic match in
    ``_fact_term_in_text`` (HARNESS-23 T9, #149):

    1. Exact substring — both sides whitespace-normalised, then
       case-insensitive (the original, pre-#149 behaviour).
    2. Flexible-whitespace CJK match — for terms containing a CJK
       character, whitespace in the term is optional (``4 行程``
       matches ``4行程``), with a leading Latin/digit boundary
       guard against mid-word joins.
    3. Translation equivalents — the curated
       ``_MUST_CONTAIN_EQUIVALENTS`` map credits an English answer
       for a CJK spec term (``右前`` ⇒ ``front right``) and vice
       versa for the English adversarial phrasings.

    Tiers 2-3 are strictly additive: every pre-#149 hit still
    hits, fixing the under-count where a correct English or
    paraphrased answer scored ``fact_recall=0`` against
    CJK-exact goldens (e.g. ``cross-005``).  Empty
    ``must_contain`` returns ``1.0`` (no facts to recall,
    vacuously satisfied).

    Returns:
        ``(score, hits, misses)`` — score in [0, 1] plus the
        concrete golden strings that did and did not match.
    """
    if not must_contain:
        return 1.0, [], []
    norm_text = _normalize_ws(output_text or "").lower()
    hits: List[str] = []
    misses: List[str] = []
    for term in must_contain:
        if _fact_term_in_text(term, norm_text):
            hits.append(term)
        else:
            misses.append(term)
    score = len(hits) / len(must_contain)
    return score, hits, misses


# Token budget constants for fact_density's conciseness factor.
#
# Calibrated 2026-05-04 against dtc-001 and lookup-001 agent outputs.
# Rationale:
# - The downstream consumer of the manual agent's deliverable is
#   another LLM (diagnose loop).  Token count = actual context cost.
# - Both the synthesis summary AND the raw_sections concat are part
#   of the deliverable (the consumer LLM needs both: framing + source
#   text to quote from).  So we count ``output_text`` whole.
# - Budget scales with the number of must_contain facts the question
#   requires the system to convey.  Each fact "deserves" some token
#   allowance for surrounding context.  More facts → bigger budget.
# - Below the budget, conciseness = 1.0 (no penalty).  Above it, it
#   decays linearly (``budget / tokens``).  The earlier 100-word cap
#   was calibrated for human chat replies; this one is calibrated
#   for LLM-to-LLM hand-off, where 5,000-15,000 tokens is normal.
_BASE_TOKEN_BUDGET = 500
"""Fixed allowance for framing, headers, synthesis prose."""

_PER_FACT_TOKEN_BUDGET = 2500
"""Per-fact allowance — covers the synthesis sentence plus enough
raw section content to substantiate it.  Calibrated against dtc-001
agent output (11,821 tokens, 5 facts) so an honest deliverable
lands at conciseness = 1.0.  Bloated outputs (e.g. 50,000 tokens)
still drop to ~0.26.  Generous on purpose: under-budgeting
penalises agents for including the source manual text, which we
explicitly want them to do."""


def _compute_fact_density(
    fact_hits: List[str],
    must_contain: List[str],
    output_text: str,
) -> float:
    """Recall × token-based conciseness factor.

    Rewards an answer that hits all the facts AND does so within
    a token budget appropriate for the number of facts requested.

    - ``recall = hits / max(must_contain, 1)`` — fraction of
      facts the output covers.
    - ``budget = BASE + PER_FACT * len(must_contain)`` — scales
      with question complexity.
    - ``conciseness = min(1, budget / max(tokens, 1))`` — caps
      at 1.0 below budget; decays linearly above.

    ``density = recall × conciseness``.

    Why tokens not words:
    - Language-aware.  ``.split()`` under-counts Chinese (no
      inter-word whitespace), which biased the old metric.
    - Aligns with the actual consumer cost.  The downstream
      diagnose LLM sees this output as input tokens; that's
      the budget that matters.
    - Removes a class of edge cases (code blocks, tables,
      numerical content all tokenize sensibly).

    Why budget scales with facts:
    - 1-fact lookups deserve ~500-2500 tokens.
    - 5-fact procedurals deserve ~5,000-10,500 tokens.
    - A fixed 100-word cap (~150 tokens) made every honest
      answer look bloated.

    Empty output or empty must_contain returns 0.0.

    Args:
        fact_hits: must_contain terms present in output_text.
        must_contain: golden's required facts.
        output_text: the system's deliverable.  For the agent:
            ``summary + cited sections concat`` (cited only,
            not every section read — exploration overhead is
            captured separately by ``exploration_cost``).  For
            RAG: top-k chunk concat.

    Returns:
        Density in [0, 1].
    """
    if not output_text or not must_contain:
        return 0.0
    tokens = _count_tokens(output_text)
    if tokens == 0:
        return 0.0
    recall = len(fact_hits) / len(must_contain)
    budget = (
        _BASE_TOKEN_BUDGET
        + _PER_FACT_TOKEN_BUDGET * len(must_contain)
    )
    conciseness = min(1.0, budget / max(tokens, 1))
    return recall * conciseness


def compute_hallucination_penalty(violation_count: int) -> float:
    """Soft penalty curve from LLM-judged pitfall violations.

    The judge evaluates each ``GoldenEntry.pitfall_directive``
    against the system output and decides per-directive whether
    the output VIOLATES it (asserts the forbidden claim) or
    COMPLIES with it (doesn't mention, or mentions in a clearly
    compliant way like negation/disambiguation).  This function
    converts the violation COUNT into a [0.1, 1.0] score.

    Curve: ``max(0.1, 1.0 - 0.3 * violation_count)``

    - 0 violations → 1.0  (clean)
    - 1 violation  → 0.7  (one bad assertion costs ~30%)
    - 2 violations → 0.4  (clearly compromised)
    - 3 violations → 0.1  (floor — further violations don't add)

    Soft curve gives partial credit for "almost right" cases —
    one passing bad assertion isn't fatal the way the old
    binary-ish ``1 - count * 0.5`` was.

    Floor of 0.1 (rather than 0.0) avoids letting one metric
    zero out the entire score; the overall formula already gives
    this metric just 0.10 weight, so the floor only contributes
    0.01 of the total.

    Replaces the older ``_compute_hallucination_penalty`` (substring
    scan over ``must_not_contain``), which was context-blind
    (couldn't distinguish "is X" from "is NOT X") and near-
    saturated (most non-adversarial entries had 0 hits regardless
    of system quality).

    Args:
        violation_count: Number of ASSERTION-type pitfall
            directives the judge marked as ``violated``.  Comes
            from ``judge.rate_quality_and_pitfalls``, which
            excludes omission-type directives ("must not omit X")
            from the count (#147) — omission is a recall failure
            already measured by ``fact_recall``, and counting it
            here double-penalised it while mislabelling
            *did-not-say-it* as *hallucinated*.

    Returns:
        Score in [0.1, 1.0].  Higher = fewer violations.
    """
    return max(0.1, 1.0 - 0.3 * max(0, violation_count))


def _compute_citation_quality(
    expected: List[str], claim: List[str], matched: bool,
) -> float:
    """Tiered citation quality, computed against ``claim_slugs``.

    Citation quality reflects the system's CLAIM about which
    sections are answers, not its navigation history — so
    this is checked against ``claim_slugs`` only, not the
    union of claim + read.

    ``matched`` is True when the claim covered at least one expected
    slug slug-tolerantly (normalised claim-slug match OR a golden
    quote present in the claimed deliverable text), resolved by
    ``_covered_expected_slugs`` in ``compute_deterministic_metrics``.
    It replaces the exact-string ``expected ∩ claim`` test, which
    demoted correct citations to 0.3 on a slug-spelling mismatch
    (#145).

    - 0.0 — system claimed no slugs (empty citations).
    - 0.3 — system claimed slugs but none cover a golden section.
    - 1.0 — at least one claimed section covers a golden slug.

    Adversarial entries (empty ``expected``) are graded
    inversely: 1.0 if ``claim`` is empty (correctly silent),
    0.3 if ``claim`` is non-empty (cited a wrong section
    when the question had no answer).
    """
    if not expected:
        # Adversarial — silence is the correct citation.
        return 1.0 if not claim else 0.3
    if not claim:
        return 0.0
    return 1.0 if matched else 0.3


def _compute_trajectory_efficiency(
    expected_calls: int, actual_calls: int,
) -> float:
    """``min(1, expected / max(actual, expected))``.

    Linear decay above expected count — 1.0 at-or-below expected,
    0.5 at 2× expected, 0.33 at 3× expected.  RAG always scores
    1.0 (single retrieval call).

    A floor of 0.0 protects against div-by-zero when
    ``expected_calls`` is 0 (uncalibrated entries) — in that
    case we return 1.0 to avoid penalising entries we haven't
    calibrated yet.
    """
    if expected_calls <= 0:
        return 1.0
    if actual_calls <= 0:
        return 1.0  # System didn't run; not the trajectory's fault.
    denominator = max(actual_calls, expected_calls)
    return min(1.0, expected_calls / denominator)


# ── Public entry point ───────────────────────────────────────────


def _is_obd_lane(entry: GoldenEntry) -> bool:
    """Return ``True`` when ``entry`` should route through OBD metrics.

    Dispatch signal: ``entry.question_type`` is one of the six OBD
    types in ``OBD_QUESTION_TYPES`` (set in ``schemas.py``).  Chosen
    over a data-field predicate (``expected_signal_citations`` or
    similar) because:

    - Self-documenting — the lane is declared at authoring time.
    - Robust to authoring slips (e.g. a manual entry accidentally
      gets an empty ``expected_dtcs`` field; we don't want it
      flipping into the OBD rubric).
    - Symmetric with the existing manual-side ``question_type``
      values which already gate certain manual-lane behaviour.

    See ``GoldenQuestionType`` in ``schemas.py`` for the literal
    definitions.
    """
    return entry.question_type in OBD_QUESTION_TYPES


def compute_deterministic_metrics(
    entry: GoldenEntry, run: SystemRunResult,
) -> DeterministicMetrics:
    """Compute all non-LLM-judge rubric dimensions.

    Dispatches to one of two lanes based on
    ``entry.question_type`` (see ``_is_obd_lane``):

    - **Manual lane** — original behaviour.  ``section_recall``,
      ``claim_precision``, ``citation_quality`` from slug
      intersections; ``trajectory_efficiency`` from tool-trace
      length; ``value_accuracy`` left at the neutral 1.0.
    - **OBD lane** — ``signal_recall`` / ``signal_precision`` /
      ``dtc_accuracy`` from ``metrics_obd``, slotted into
      ``section_recall`` / ``claim_precision`` / ``citation_quality``
      so the shared rubric formula in ``compute_overall`` works
      unchanged.  ``value_accuracy`` populated from
      ``metrics_obd.compute_value_accuracy``.
      ``exploration_cost`` and ``trajectory_efficiency`` set to
      their neutral values (no reads-vs-claims distinction on the
      OBD side; trajectory is reported via ``tool_trace`` but
      doesn't fold into the rubric here).

    Common dimensions (``fact_recall``, ``fact_density``) are
    computed identically for both lanes — they operate on
    ``output_text`` and ``must_contain``, which both lanes
    populate.

    Args:
        entry: Golden reference.
        run: One system's output for the same question.

    Returns:
        ``DeterministicMetrics`` with all dimensions populated.
        The judge later adds ``answer_quality`` and
        ``hallucination_penalty`` to form the final ``Grade``.
    """
    # Shared: fact_recall / fact_density operate on ``output_text``
    # and ``must_contain`` regardless of lane.
    fact_recall, fact_hits, fact_misses = _compute_fact_recall(
        entry.must_contain, run.output_text,
    )
    fact_density = _compute_fact_density(
        fact_hits, entry.must_contain, run.output_text,
    )

    if _is_obd_lane(entry):
        obd = compute_obd_deterministic_metrics(entry, run)
        # Slot OBD dims into the shared envelope.  The naming is
        # mildly awkward (section_recall holds a signal_recall
        # number for OBD entries) but keeps the rubric formula
        # in ``compute_overall`` lane-agnostic — a single weight
        # vector applies to both lanes.
        return DeterministicMetrics(
            section_recall=obd.signal_recall,
            claim_precision=obd.signal_precision,
            exploration_cost=0.0,  # No reads-vs-claims gap on OBD.
            fact_recall=fact_recall,
            fact_density=fact_density,
            citation_quality=obd.dtc_accuracy,
            trajectory_efficiency=1.0,  # Not scored in OBD lane.
            value_accuracy=obd.value_accuracy,
            fact_recall_hits=fact_hits,
            fact_recall_misses=fact_misses,
        )

    # Manual lane (original behaviour, with slug-tolerant matching).
    # Surfaced = claim ∪ read.  section_recall asks "did the
    # system make this section available anywhere," which
    # includes both the cited and the merely-read.
    surfaced = list({*run.claim_slugs, *run.read_slugs})
    # Text the system surfaced, for the quote-containment fallback:
    # the deliverable plus any RAG chunk previews.  Read-but-not-
    # cited section text isn't carried on the run object, so recall's
    # quote fallback works over what's available; the slug half still
    # credits read-only sections.
    surfaced_text = "\n".join(
        [run.output_text or ""]
        + [m.text_preview for m in run.retrieved_chunk_metadata]
    )

    covered_recall = _covered_expected_slugs(
        entry.expected_recall_slugs,
        entry.golden_citations,
        surfaced,
        surfaced_text,
    )
    section_recall = _compute_section_recall(
        entry.expected_recall_slugs, covered_recall,
    )
    claim_precision = _compute_claim_precision(
        entry.expected_recall_slugs, run.claim_slugs,
    )
    exploration_cost = _compute_exploration_cost(
        run.read_slugs, run.claim_slugs,
    )

    # Citation quality: forgiving quote scan over the whole
    # deliverable (``output_text``) plus normalised claim-slug match.
    covered_claim = _covered_expected_slugs(
        entry.expected_recall_slugs,
        entry.golden_citations,
        run.claim_slugs,
        run.output_text or "",
    )
    citation_quality = _compute_citation_quality(
        entry.expected_recall_slugs,
        run.claim_slugs,
        bool(covered_claim),
    )

    # Trajectory only meaningful for the agent.  RAG scores 1.0
    # (single embedding + single pgvector query — no choices to
    # make).
    if run.system_label == "manual_agent":
        trajectory_efficiency = _compute_trajectory_efficiency(
            len(entry.expected_tool_trace),
            len(run.tool_trace),
        )
    else:
        trajectory_efficiency = 1.0

    return DeterministicMetrics(
        section_recall=section_recall,
        claim_precision=claim_precision,
        exploration_cost=exploration_cost,
        fact_recall=fact_recall,
        fact_density=fact_density,
        citation_quality=citation_quality,
        trajectory_efficiency=trajectory_efficiency,
        # Manual lane has no value_accuracy concept; neutral 1.0
        # keeps the rebalanced formula honest.
        value_accuracy=1.0,
        fact_recall_hits=fact_hits,
        fact_recall_misses=fact_misses,
    )


# ── Overall-score combiner ───────────────────────────────────────


# Weights for the comparative-eval rubric.  Exposed as a
# constant (not hard-coded into a single formula) so we can
# tune without rewriting callers.  Sums to 1.0.
#
# Rebalanced 2026-05-04 after the fact_density rework:
# - fact_density now uses a token-based budget that scales with
#   the number of must_contain facts.  No longer broken by
#   raw_sections concat.  Restored to 0.10 weight (was 0.05
#   while broken).
# - exploration_cost stays at 0.05 — it's a real cost, not
#   negligible, but shouldn't dominate.
# - hallucination_penalty trimmed 0.15 → 0.10 to fund the
#   fact_density restoration.  Rationale: hallucination_penalty
#   is near-saturated on non-adversarial entries (most systems
#   score 1.0 — they don't fabricate must_not_contain terms),
#   so an extra 0.05 of weight here mostly inflates everyone
#   uniformly without improving discrimination.  The judge's
#   answer_quality already catches subtler hallucinations.
#
# Rebalanced 2026-05-17 for HARNESS-21:
# - value_accuracy added at 0.10 weight (new OBD-lane dim;
#   neutral 1.0 for manual entries so they aren't penalised).
# - section_recall trimmed 0.25 → 0.20 to fund value_accuracy
#   (still the heaviest single dim).
# - claim_precision trimmed 0.15 → 0.10.
# - fact_recall trimmed 0.20 → 0.15.
# - fact_density trimmed 0.10 → 0.05.
# - hallucination_penalty restored 0.10 → 0.15 (closer to the
#   design's intent now that the judge's pitfall-directive
#   verdicts are reliable enough to discriminate).
# - answer_quality restored 0.10 → 0.15 (similarly).
# Manual-lane scores will SHIFT slightly under the new weights
# even though value_accuracy stays neutral — accepted per the
# approved design (docs/plans/2026-05-17-harness-21-obd-eval-
# design.md § 3); PR [3/3] re-baselines both lanes.
DEFAULT_OVERALL_WEIGHTS: dict = {
    "section_recall":         0.20,
    "claim_precision":        0.10,
    "exploration_cost":       0.05,  # applied as (1 - cost)
    "fact_recall":            0.15,
    "fact_density":           0.05,
    "hallucination_penalty":  0.15,
    "citation_quality":       0.05,
    "value_accuracy":         0.10,
    "answer_quality":         0.15,
}


def compute_overall(
    metrics: DeterministicMetrics,
    answer_quality: float,
    hallucination_penalty: float,
    weights: Optional[dict] = None,
) -> float:
    """Combine deterministic metrics + judge-derived metrics.

    Two metrics come from the LLM judge (passed in explicitly)
    rather than from ``DeterministicMetrics``:

    - ``answer_quality`` — judge's holistic rating.
    - ``hallucination_penalty`` — derived from judge's
      ``pitfall_violations`` count via
      ``compute_hallucination_penalty``.

    Note: ``exploration_cost`` is a "lower is better" metric;
    it enters the formula as ``(1 - cost)`` so all terms
    contribute positively toward the overall score.

    N/A handling (#148): when ``metrics.section_recall`` is
    ``None`` (manual adversarial — empty
    ``expected_recall_slugs``, nothing SHOULD be retrieved), the
    ``section_recall`` term is excluded and the remaining terms
    are rescaled by ``total / (total - w_sr)`` so the score stays
    on the same [0, 1] scale.  This replaces the old vacuous-1.0
    behaviour, which handed adversarial entries a free +0.20 of
    floor in both lanes.

    Args:
        metrics: Output of ``compute_deterministic_metrics``.
        answer_quality: LLM-judge rating in [0, 1].
        hallucination_penalty: Score in [0.1, 1.0] from
            ``compute_hallucination_penalty(violation_count)``.
        weights: Optional override (defaults to
            ``DEFAULT_OVERALL_WEIGHTS``).  Must contain all
            nine rubric keys; sum is not enforced (caller can
            experiment with different weighting schemes).

    Returns:
        Weighted score in [0, 1].
    """
    w = weights if weights is not None else DEFAULT_OVERALL_WEIGHTS
    score = (
        w["claim_precision"]       * metrics.claim_precision
        + w["exploration_cost"]    * (1.0 - metrics.exploration_cost)
        + w["fact_recall"]         * metrics.fact_recall
        + w["fact_density"]        * metrics.fact_density
        + w["hallucination_penalty"] * hallucination_penalty
        + w["citation_quality"]    * metrics.citation_quality
        + w["value_accuracy"]      * metrics.value_accuracy
        + w["answer_quality"]      * answer_quality
    )
    if metrics.section_recall is None:
        # N/A — exclude the dim and renormalise its weight over
        # the remaining terms so an entry with no expected slugs
        # is graded purely on the dims that apply to it.
        total = sum(w.values())
        applicable = total - w["section_recall"]
        if applicable <= 0:
            return score
        return score * (total / applicable)
    return score + w["section_recall"] * metrics.section_recall
