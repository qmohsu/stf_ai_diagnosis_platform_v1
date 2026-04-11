"""Graduated autonomy router — complexity classifier and dispatch.

Analyzes ``parsed_summary_payload`` to classify diagnostic complexity
into tiers (0–3) and routes to V1 one-shot or V2 agent accordingly.

Design doc ref: ``docs/v2_design_doc.md`` §8.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional

import structlog

logger = structlog.get_logger(__name__)

# ── Severity ordering ───────────────────────────────────────────────

_SEVERITY_RANK: Dict[str, int] = {
    "none": 0,
    "low": 1,
    "moderate": 2,
    "high": 3,
    "critical": 4,
}

# Keywords mapped to severity levels for anomaly event text.
_SEVERITY_KEYWORDS: List[tuple[str, str]] = [
    ("critical", "critical"),
    ("severe", "critical"),
    ("dangerous", "critical"),
    ("high", "high"),
    ("significant", "high"),
    ("major", "high"),
    ("moderate", "moderate"),
    ("minor", "low"),
    ("low", "low"),
    ("slight", "low"),
]


# ── Result dataclass ────────────────────────────────────────────────


@dataclass(frozen=True)
class AutonomyDecision:
    """Result of the graduated autonomy classification.

    Attributes:
        tier: Autonomy tier (0–3).
        strategy: Human-readable strategy label.
        reason: Short explanation of why this tier was chosen.
        use_agent: Whether to use the agent loop (True) or
            V1 one-shot (False).
        suggested_max_iterations: Recommended iteration cap
            for the agent loop (only meaningful when
            ``use_agent`` is True).
    """

    tier: int
    strategy: str
    reason: str
    use_agent: bool
    suggested_max_iterations: int


# ── Helper functions ────────────────────────────────────────────────


def _count_dtcs(dtc_codes: str) -> int:
    """Count distinct DTC codes in a comma-separated string.

    Args:
        dtc_codes: Raw ``dtc_codes`` field from parsed summary
            (e.g. ``"P0300, P0420"`` or ``"P0300 (Random Misfire)"``).

    Returns:
        Number of distinct DTC codes found.
    """
    if not dtc_codes or not dtc_codes.strip():
        return 0

    # Match standard OBD-II DTC pattern: letter + 4 digits
    matches = re.findall(r"[PCBU]\d{4}", dtc_codes.upper())
    return len(set(matches))


def _max_severity(anomaly_events: str) -> str:
    """Extract the highest severity level from anomaly event text.

    Scans for severity keywords in the anomaly description and
    returns the highest one found.  Defaults to ``"moderate"``
    when anomaly text is present but no keywords match (implies
    some anomaly was detected, just not explicitly graded).

    Args:
        anomaly_events: Raw ``anomaly_events`` field from parsed
            summary.

    Returns:
        Severity string: ``"none"``, ``"low"``, ``"moderate"``,
        ``"high"``, or ``"critical"``.
    """
    if not anomaly_events or not anomaly_events.strip():
        return "none"

    text_lower = anomaly_events.lower()

    if text_lower in ("none", "n/a", "no anomalies", "no anomaly"):
        return "none"

    best = "moderate"  # default when anomalies exist
    best_rank = _SEVERITY_RANK[best]

    for keyword, severity in _SEVERITY_KEYWORDS:
        if keyword in text_lower:
            rank = _SEVERITY_RANK[severity]
            if rank > best_rank:
                best = severity
                best_rank = rank

    return best


def _count_clues(diagnostic_clues: str) -> int:
    """Count diagnostic clues in the clue string.

    Clues are typically separated by semicolons or newlines.
    Each ``STAT_XXX`` or ``RULE_XXX`` tag also counts as a clue.

    Args:
        diagnostic_clues: Raw ``diagnostic_clues`` field from
            parsed summary.

    Returns:
        Number of distinct clues found.
    """
    if not diagnostic_clues or not diagnostic_clues.strip():
        return 0

    text = diagnostic_clues.strip()

    # Count by rule/stat tags (e.g. STAT_001, RULE_002)
    tags = re.findall(r"(?:STAT|RULE)_\d+", text)
    if tags:
        return len(set(tags))

    # Fall back to counting semicolon-separated or
    # newline-separated entries
    parts = re.split(r"[;\n]", text)
    return len([p for p in parts if p.strip()])


# ── Main classifier ─────────────────────────────────────────────────


def classify_complexity(
    parsed_summary: dict,
    has_prior_diagnosis: bool = False,
) -> AutonomyDecision:
    """Classify diagnostic complexity into an autonomy tier.

    Deterministic rule-based classification — the same inputs
    always produce the same tier.

    Args:
        parsed_summary: Flat-string summary dict from the V1
            pipeline (``parsed_summary_payload``).
        has_prior_diagnosis: Whether the session already has a
            prior diagnosis in ``DiagnosisHistory`` (triggers
            Tier 3 follow-up mode).

    Returns:
        ``AutonomyDecision`` with tier, strategy, reason, and
        routing decision.
    """
    dtc_codes = parsed_summary.get("dtc_codes", "")
    anomaly_events = parsed_summary.get("anomaly_events", "")
    diagnostic_clues = parsed_summary.get("diagnostic_clues", "")

    dtc_count = _count_dtcs(dtc_codes)
    severity = _max_severity(anomaly_events)
    clue_count = _count_clues(diagnostic_clues)
    sev_rank = _SEVERITY_RANK.get(severity, 2)

    logger.debug(
        "autonomy_classify_inputs",
        dtc_count=dtc_count,
        severity=severity,
        clue_count=clue_count,
        has_prior_diagnosis=has_prior_diagnosis,
    )

    # Tier 3: Follow-up — has prior diagnosis history
    if has_prior_diagnosis:
        decision = AutonomyDecision(
            tier=3,
            strategy="agent + case history",
            reason=(
                f"Follow-up: prior diagnosis exists "
                f"({dtc_count} DTC(s), severity={severity})"
            ),
            use_agent=True,
            suggested_max_iterations=10,
        )

    # Tier 2: Complex — many DTCs or critical severity
    elif dtc_count > 3 or severity == "critical":
        decision = AutonomyDecision(
            tier=2,
            strategy="full agent",
            reason=(
                f"{dtc_count} DTC(s), severity={severity}"
                f" — complex multi-fault scenario"
            ),
            use_agent=True,
            suggested_max_iterations=15,
        )

    # Tier 1: Moderate — multiple DTCs or high severity
    elif dtc_count > 1 or sev_rank >= _SEVERITY_RANK["high"]:
        decision = AutonomyDecision(
            tier=1,
            strategy="agent loop",
            reason=(
                f"{dtc_count} DTC(s), severity={severity}"
                f" — moderate complexity"
            ),
            use_agent=True,
            suggested_max_iterations=5,
        )

    # Tier 0: Simple — single DTC, moderate or lower, few clues
    else:
        decision = AutonomyDecision(
            tier=0,
            strategy="V1 one-shot",
            reason=(
                f"{dtc_count} DTC(s), severity={severity}"
                f", {clue_count} clue(s) — simple case"
            ),
            use_agent=False,
            suggested_max_iterations=0,
        )

    logger.info(
        "autonomy_decision",
        tier=decision.tier,
        strategy=decision.strategy,
        reason=decision.reason,
        use_agent=decision.use_agent,
    )
    return decision


def apply_overrides(
    decision: AutonomyDecision,
    *,
    force_agent: bool = False,
    force_oneshot: bool = False,
) -> AutonomyDecision:
    """Apply query-parameter overrides to an autonomy decision.

    ``force_oneshot`` takes precedence over ``force_agent`` when
    both are True (safety-first: prefer the cheaper path).

    Args:
        decision: Original classification result.
        force_agent: Escalate Tier 0 to use the agent loop.
        force_oneshot: Force V1 one-shot regardless of tier.

    Returns:
        Possibly modified ``AutonomyDecision``.
    """
    if force_oneshot:
        if decision.use_agent:
            logger.info(
                "autonomy_override_oneshot",
                original_tier=decision.tier,
            )
            return AutonomyDecision(
                tier=decision.tier,
                strategy="V1 one-shot (forced)",
                reason=f"force_oneshot override "
                       f"(original: {decision.reason})",
                use_agent=False,
                suggested_max_iterations=0,
            )
        return decision

    if force_agent:
        if not decision.use_agent:
            logger.info(
                "autonomy_override_agent",
                original_tier=decision.tier,
            )
            return AutonomyDecision(
                tier=decision.tier,
                strategy="agent loop (forced)",
                reason=f"force_agent override "
                       f"(original: {decision.reason})",
                use_agent=True,
                suggested_max_iterations=5,
            )
        return decision

    return decision
