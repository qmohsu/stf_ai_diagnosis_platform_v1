"""Validation gates I1–I8 (spec §6, S2.2).

Mechanical checks only — no LLM, no judgment.  Each invariant maps
to a measured defect class from the 2026-07-21 scan; any failure
means the index MUST NOT be published.  The mutation-test suite in
``tests/manual_pipeline/test_invariants.py`` proves each gate
catches its defect class (validator-of-the-validator, spec §6.2).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import List, Set

from manual_pipeline.index_schema import ManualIndex, Vocab
from manual_pipeline.stream import ItemKind, NormalizedItem
from manual_pipeline.tree_builder import is_noise_title

_MIN_LEAF_CHARS = 50
_NOISE_BUDGET_RATIO = 0.20


@dataclass
class GateResult:
    """Outcome of one invariant."""

    gate: str
    passed: bool
    detail: str = ""


@dataclass
class ValidationResult:
    """All gate outcomes; publishable only when all pass."""

    gates: List[GateResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(g.passed for g in self.gates)

    def failures(self) -> List[GateResult]:
        return [g for g in self.gates if not g.passed]


def validate(
    index: ManualIndex,
    items: List[NormalizedItem],
    content_md: str,
    swept_codes: List[str],
    vocab: Vocab,
    noise_item_idxs: Set[int],
    require_summaries: bool = True,
) -> ValidationResult:
    """Run every gate.

    Args:
        index: The candidate artifact.
        items: Normalized stream the spans reference.
        content_md: Final content markdown (I7 hash target).
        swept_codes: Independent DTC sweep (I3 reference).
        vocab: Controlled vocabulary (I6 membership).
        noise_item_idxs: Items demoted by R1 (I8 budget).
        require_summaries: False for structural dev builds — the
            report marks the artifact NOT publishable.

    Returns:
        ValidationResult with one GateResult per invariant.
    """
    res = ValidationResult()
    nodes = index.all_nodes()
    ids = [n.node_id for n in nodes]
    id_set = set(ids)
    n_items = len(items)

    # ── I1: coverage + sibling tiling ────────────────────────
    problems: List[str] = []
    covered: Set[int] = set()
    for root in index.tree:
        covered.update(range(root.span[0], root.span[1]))
    missing = [i for i in range(n_items) if i not in covered]
    if missing:
        problems.append(f"{len(missing)} items uncovered "
                        f"(first: {missing[:5]})")

    def check_siblings(siblings) -> None:
        spans = sorted(s.span for s in siblings)
        for (s1, e1), (s2, e2) in zip(spans, spans[1:]):
            if s2 < e1:
                problems.append(
                    f"sibling overlap [{s1},{e1})∩[{s2},{e2})",
                )

    check_siblings(index.tree)
    for node in nodes:
        if node.children:
            check_siblings(node.children)
            for child in node.children:
                if not (
                    node.span[0] <= child.span[0]
                    and child.span[1] <= node.span[1]
                ):
                    problems.append(
                        f"child {child.node_id} escapes parent "
                        f"{node.node_id}",
                    )
    res.gates.append(GateResult(
        "I1-coverage", not problems, "; ".join(problems[:5]),
    ))

    # ── I2: no empty shells ──────────────────────────────────
    bad: List[str] = []
    for node in nodes:
        if node.children:
            continue  # container adequacy covered by I1
        if node.node_type == "index":
            continue
        span_items = items[node.span[0]:node.span[1]]
        chars = sum(
            len(it.text) + len(it.html) for it in span_items
        )
        has_image = any(
            it.kind == ItemKind.IMAGE for it in span_items
        )
        if chars < _MIN_LEAF_CHARS and not has_image:
            bad.append(f"{node.node_id}({chars}ch)")
    res.gates.append(GateResult(
        "I2-no-empty-shells", not bad, ", ".join(bad[:5]),
    ))

    # ── I3: DTC completeness ─────────────────────────────────
    cards = {f.code: f for f in index.faults}
    miss3: List[str] = []
    for code in swept_codes:
        card = cards.get(code)
        if card is None:
            miss3.append(f"{code}:no-card")
        elif card.isolate_ref is None:
            miss3.append(f"{code}:no-isolate_ref")
        elif card.isolate_ref not in id_set:
            miss3.append(f"{code}:dangling-isolate_ref")
    res.gates.append(GateResult(
        "I3-dtc-complete", not miss3, ", ".join(miss3[:8]),
    ))

    # ── I4: id uniqueness + ref integrity ────────────────────
    bad4: List[str] = []
    if len(ids) != len(id_set):
        dupes = {i for i in ids if ids.count(i) > 1}
        bad4.append(f"duplicate ids: {sorted(dupes)[:5]}")
    for fault in index.faults:
        for ref in (
            fault.detect_ref, fault.isolate_ref,
            *fault.related_refs,
        ):
            if ref is not None and ref not in id_set:
                bad4.append(f"{fault.code}→{ref} unresolved")
    res.gates.append(GateResult(
        "I4-ids-and-refs", not bad4, "; ".join(bad4[:5]),
    ))

    # ── I5: no junk node titles ──────────────────────────────
    junk = [
        n.node_id for n in nodes if is_noise_title(n.title)
    ]
    res.gates.append(GateResult(
        "I5-no-junk-titles", not junk, ", ".join(junk[:5]),
    ))

    # ── I6: vocab membership (+ summaries when required) ─────
    bad6: List[str] = []
    for node in nodes:
        if node.subsystem not in vocab.subsystems:
            bad6.append(f"{node.node_id}:subsystem")
        if node.node_type not in vocab.node_types:
            bad6.append(f"{node.node_id}:node_type")
        if require_summaries and not node.summary.strip():
            bad6.append(f"{node.node_id}:summary")
    res.gates.append(GateResult(
        "I6-vocab-membership", not bad6, ", ".join(bad6[:5]),
    ))

    # ── I7: content hash ─────────────────────────────────────
    actual = hashlib.sha256(
        content_md.encode("utf-8"),
    ).hexdigest()
    stated = index.source.get("content_sha256", "")
    res.gates.append(GateResult(
        "I7-content-hash",
        actual == stated,
        "" if actual == stated else
        f"stated {stated[:12]}… != actual {actual[:12]}…",
    ))

    # ── I8: noise budget ─────────────────────────────────────
    ratio = len(noise_item_idxs) / n_items if n_items else 0.0
    res.gates.append(GateResult(
        "I8-noise-budget",
        ratio <= _NOISE_BUDGET_RATIO,
        f"noise ratio {ratio:.1%} > {_NOISE_BUDGET_RATIO:.0%}"
        if ratio > _NOISE_BUDGET_RATIO else
        f"noise ratio {ratio:.1%}",
    ))

    return res
