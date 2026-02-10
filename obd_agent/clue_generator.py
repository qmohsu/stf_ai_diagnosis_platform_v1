"""Rule-based diagnostic clue generation for OBD-II time series.

Stage 3 of the OBD-II Diagnostic Summarisation Pipeline (APP-16).  Consumes
:class:`~obd_agent.statistics_extractor.SignalStatistics` from APP-14 and
:class:`~obd_agent.anomaly_detector.AnomalyReport` from APP-15, then applies
deterministic rule-based heuristics to produce traceable diagnostic clues for
downstream LLM reasoning.

No LLM calls — traceability and determinism are required by design.
"""

from __future__ import annotations

import logging
import operator
from dataclasses import dataclass, fields as dc_fields
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from obd_agent.anomaly_detector import AnomalyEvent, AnomalyReport, detect_anomalies
from obd_agent.statistics_extractor import (
    SignalStatistics,
    SignalStats,
    extract_statistics,
)
from obd_agent.time_series_normalizer import FillMethod, normalize_log_file

logger = logging.getLogger(__name__)

_DEFAULT_RULES_PATH = Path(__file__).resolve().parent / "rules" / "diagnostic_rules.yaml"

# Operator lookup for stat_check / stat_compare conditions
_OPS = {
    "eq": operator.eq,
    "ne": operator.ne,
    "lt": operator.lt,
    "le": operator.le,
    "gt": operator.gt,
    "ge": operator.ge,
}

# Valid SignalStats field names (for validation)
_SIGNAL_STATS_FIELDS = frozenset(f.name for f in dc_fields(SignalStats))

# ---------------------------------------------------------------------------
# Output dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DiagnosticClue:
    """A single traceable diagnostic fact derived from a rule match.

    Attributes
    ----------
    rule_id : str
        Identifier of the matched rule (e.g. ``"STAT_001"``).
    category : str
        Rule category (``"statistical"`` | ``"anomaly"`` | ``"interaction"``
        | ``"dtc"`` | ``"negative_evidence"``).
    clue : str
        Human-readable diagnostic fact populated from the rule template.
    evidence : tuple[str, ...]
        Source evidence strings (e.g. ``("engine_rpm.mean=0.0",)``).
    severity : str
        ``"info"`` | ``"warning"`` | ``"critical"``.
    """

    rule_id: str
    category: str
    clue: str
    evidence: Tuple[str, ...]
    severity: str


@dataclass(frozen=True)
class DiagnosticClueReport:
    """Collection of diagnostic clues with session metadata.

    Attributes
    ----------
    clues : tuple[DiagnosticClue, ...]
        Matched clues, in rule evaluation order.
    vehicle_id : str
        Pseudonymised vehicle identifier.
    time_range : tuple[datetime, datetime]
        ``(start, end)`` of the analysed time series.
    dtc_codes : list[str]
        DTC codes present in the session.
    rules_applied : int
        Total number of rules evaluated.
    rules_matched : int
        Number of rules that produced a clue.
    """

    clues: Tuple[DiagnosticClue, ...]
    vehicle_id: str
    time_range: Tuple[datetime, datetime]
    dtc_codes: Tuple[str, ...]
    rules_applied: int
    rules_matched: int

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict suitable for ``json.dumps``.

        Returns both a flat ``"diagnostic_clues"`` list (design doc format)
        and detailed ``"clue_details"`` with full traceability.
        """
        clue_details = []
        diagnostic_clues: List[str] = []
        for c in self.clues:
            diagnostic_clues.append(c.clue)
            clue_details.append({
                "rule_id": c.rule_id,
                "category": c.category,
                "clue": c.clue,
                "evidence": list(c.evidence),
                "severity": c.severity,
            })

        return {
            "diagnostic_clues": diagnostic_clues,
            "clue_details": clue_details,
            "vehicle_id": self.vehicle_id,
            "time_range": [
                self.time_range[0].isoformat(),
                self.time_range[1].isoformat(),
            ],
            "dtc_codes": list(self.dtc_codes),
            "rules_applied": self.rules_applied,
            "rules_matched": self.rules_matched,
        }


# ---------------------------------------------------------------------------
# Template namespace helper
# ---------------------------------------------------------------------------


class _SignalNamespace:
    """Wrap a :class:`SignalStats` so ``str.format_map`` can resolve dotted names.

    Allows templates like ``{engine_rpm.mean}`` via attribute delegation.
    """

    def __init__(self, signal_stats: SignalStats) -> None:
        self._stats = signal_stats

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return getattr(self._stats, name)
        except AttributeError:
            raise AttributeError(
                f"SignalStats has no field '{name}'"
            ) from None

    def __format__(self, format_spec: str) -> str:
        return str(self._stats.mean).__format__(format_spec)


class _TemplateContext(dict):
    """Dict-like context for ``str.format_map`` that supports dotted access.

    Keys are signal names mapping to :class:`_SignalNamespace` objects.
    Extra keys (``anomaly_count``, ``matched_dtcs``) are plain values.
    Missing keys return ``"N/A"`` to avoid template errors.
    """

    def __missing__(self, key: str) -> str:
        logger.debug("Template key '%s' not found in context, using 'N/A'", key)
        return "N/A"


def _build_template_context(
    stats: SignalStatistics,
    anomaly_count: int = 0,
    matched_dtcs: str = "",
) -> _TemplateContext:
    """Build a namespace dict for ``str.format_map()``."""
    ctx = _TemplateContext()
    for name, ss in stats.stats.items():
        ctx[name] = _SignalNamespace(ss)
    ctx["anomaly_count"] = anomaly_count
    ctx["matched_dtcs"] = matched_dtcs
    return ctx


# ---------------------------------------------------------------------------
# Rule loading
# ---------------------------------------------------------------------------

_VALID_CONDITION_TYPES = frozenset(
    ["stat_check", "anomaly_check", "dtc_check", "stat_compare", "signal_exists"]
)
_VALID_SEVERITIES = frozenset(["info", "warning", "critical"])
_VALID_CATEGORIES = frozenset(
    ["statistical", "anomaly", "interaction", "dtc", "negative_evidence"]
)


def _validate_rule(rule: Dict[str, Any], index: int) -> None:
    """Validate a single rule dict structure. Raises ValueError on problems."""
    required_keys = {"id", "category", "severity", "conditions", "template"}
    missing = required_keys - set(rule.keys())
    if missing:
        raise ValueError(
            f"Rule at index {index} (id={rule.get('id', '?')}) "
            f"missing required keys: {missing}"
        )
    if rule["severity"] not in _VALID_SEVERITIES:
        raise ValueError(
            f"Rule {rule['id']}: invalid severity '{rule['severity']}'"
        )
    if rule["category"] not in _VALID_CATEGORIES:
        raise ValueError(
            f"Rule {rule['id']}: invalid category '{rule['category']}'"
        )
    if not isinstance(rule["conditions"], list) or len(rule["conditions"]) == 0:
        raise ValueError(f"Rule {rule['id']}: conditions must be a non-empty list")
    for cond in rule["conditions"]:
        if cond.get("type") not in _VALID_CONDITION_TYPES:
            raise ValueError(
                f"Rule {rule['id']}: invalid condition type '{cond.get('type')}'"
            )


def _load_rules(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Load and validate rules from a YAML file.

    Parameters
    ----------
    path :
        Path to YAML file.  Defaults to the bundled ``diagnostic_rules.yaml``.

    Returns
    -------
    list[dict]
        Validated rule dicts.

    Raises
    ------
    ValueError
        If the YAML is invalid or any rule fails validation.
    FileNotFoundError
        If the file does not exist.
    """
    if path is None:
        path = _DEFAULT_RULES_PATH
    path = Path(path)

    try:
        with open(path, encoding="utf-8") as fh:
            rules = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in {path}: {exc}") from exc

    if not isinstance(rules, list):
        raise ValueError(f"Expected a list of rules in {path}, got {type(rules).__name__}")

    seen_ids: set[str] = set()
    for i, rule in enumerate(rules):
        _validate_rule(rule, i)
        rid = rule["id"]
        if rid in seen_ids:
            raise ValueError(f"Duplicate rule id: {rid}")
        seen_ids.add(rid)

    return rules


# ---------------------------------------------------------------------------
# Condition evaluators
# ---------------------------------------------------------------------------


def _eval_stat_check(
    cond: Dict[str, Any],
    stats: SignalStatistics,
) -> Tuple[bool, List[str]]:
    """Evaluate a ``stat_check`` condition: signal field vs threshold.

    Returns ``(matched, evidence_list)``.
    """
    signal = cond["signal"]
    field = cond["field"]
    op_name = cond["op"]
    threshold = cond["value"]

    if signal not in stats.stats:
        return False, []

    ss = stats.stats[signal]
    if field not in _SIGNAL_STATS_FIELDS:
        logger.warning("stat_check: unknown field '%s'", field)
        return False, []

    actual = getattr(ss, field)
    # NaN never matches
    if isinstance(actual, float) and (actual != actual):  # NaN check
        return False, []

    op_func = _OPS.get(op_name)
    if op_func is None:
        logger.warning("stat_check: unknown operator '%s'", op_name)
        return False, []

    matched = op_func(actual, threshold)
    evidence = [f"{signal}.{field}={actual}"]
    return matched, evidence


def _eval_anomaly_check(
    cond: Dict[str, Any],
    anomalies: AnomalyReport,
) -> Tuple[bool, List[str], int]:
    """Evaluate an ``anomaly_check`` condition: filter anomaly events.

    Returns ``(matched, evidence_list, matching_event_count)``.
    """
    matching: List[AnomalyEvent] = list(anomalies.events)

    # Filter by signal
    signal_filter = cond.get("signal")
    if signal_filter:
        matching = [e for e in matching if signal_filter in e.signals]

    # Filter by context
    context_filter = cond.get("context")
    if context_filter:
        matching = [e for e in matching if e.context == context_filter]

    # Filter by severity
    severity_filter = cond.get("severity")
    if severity_filter:
        matching = [e for e in matching if e.severity == severity_filter]

    count = len(matching)

    # Check min_count
    min_count = cond.get("min_count")
    if min_count is not None:
        matched = count >= min_count
    # Check max_count (for negative evidence like "no anomalies")
    elif "max_count" in cond:
        max_count = cond["max_count"]
        matched = count <= max_count
    else:
        matched = count > 0

    evidence: List[str] = []
    if matched:
        evidence.append(f"anomaly_events_matched={count}")
        if signal_filter:
            evidence.append(f"anomaly_signal_filter={signal_filter}")
        if context_filter:
            evidence.append(f"anomaly_context_filter={context_filter}")
        if severity_filter:
            evidence.append(f"anomaly_severity_filter={severity_filter}")

    return matched, evidence, count


def _eval_dtc_check(
    cond: Dict[str, Any],
    dtc_codes: List[str],
) -> Tuple[bool, List[str], str]:
    """Evaluate a ``dtc_check`` condition: DTC presence/absence/prefix.

    Returns ``(matched, evidence_list, matched_dtcs_str)``.
    """
    mode = cond.get("mode", "present")

    if mode == "absent":
        # No DTCs at all
        matched = len(dtc_codes) == 0
        evidence = [f"dtc_count={len(dtc_codes)}"]
        return matched, evidence, ""

    prefix = cond.get("prefix", "")

    if mode == "prefix":
        # At least one DTC matches the prefix
        found = [c for c in dtc_codes if c.startswith(prefix)]
        matched = len(found) > 0
        evidence = [f"dtc_prefix={prefix}", f"dtc_matched={', '.join(found)}"]
        return matched, evidence, ", ".join(found)

    if mode == "absent_prefix":
        # No DTC matches the prefix (negative evidence)
        found = [c for c in dtc_codes if c.startswith(prefix)]
        matched = len(found) == 0
        evidence = [f"dtc_absent_prefix={prefix}", f"dtc_matched={', '.join(found)}"]
        return matched, evidence, ""

    if mode == "present":
        # At least one DTC code exists
        code = cond.get("code", "")
        if code:
            matched = code in dtc_codes
            evidence = [f"dtc_code={code}", f"dtc_present={matched}"]
            return matched, evidence, code if matched else ""
        matched = len(dtc_codes) > 0
        evidence = [f"dtc_count={len(dtc_codes)}"]
        return matched, evidence, ", ".join(dtc_codes)

    logger.warning("dtc_check: unknown mode '%s'", mode)
    return False, [], ""


def _eval_stat_compare(
    cond: Dict[str, Any],
    stats: SignalStatistics,
) -> Tuple[bool, List[str]]:
    """Evaluate a ``stat_compare`` condition: cross-signal field comparison.

    Compares ``signal_a.field_a`` against ``signal_b.field_b * ratio``
    using the given operator.

    Returns ``(matched, evidence_list)``.
    """
    sig_a = cond["signal_a"]
    field_a = cond["field_a"]
    sig_b = cond["signal_b"]
    field_b = cond["field_b"]
    op_name = cond["op"]
    ratio = cond.get("ratio", 1.0)

    if sig_a not in stats.stats or sig_b not in stats.stats:
        return False, []

    if field_a not in _SIGNAL_STATS_FIELDS:
        logger.warning("stat_compare: unknown field '%s'", field_a)
        return False, []
    if field_b not in _SIGNAL_STATS_FIELDS:
        logger.warning("stat_compare: unknown field '%s'", field_b)
        return False, []

    val_a = getattr(stats.stats[sig_a], field_a, None)
    val_b = getattr(stats.stats[sig_b], field_b, None)

    if val_a is None or val_b is None:
        return False, []

    # NaN check
    if isinstance(val_a, float) and val_a != val_a:
        return False, []
    if isinstance(val_b, float) and val_b != val_b:
        return False, []

    op_func = _OPS.get(op_name)
    if op_func is None:
        logger.warning("stat_compare: unknown operator '%s'", op_name)
        return False, []

    matched = op_func(val_a, val_b * ratio)
    evidence = [
        f"{sig_a}.{field_a}={val_a}",
        f"{sig_b}.{field_b}={val_b}",
        f"ratio={ratio}",
    ]
    return matched, evidence


def _eval_signal_exists(
    cond: Dict[str, Any],
    stats: SignalStatistics,
) -> Tuple[bool, List[str]]:
    """Evaluate a ``signal_exists`` condition: data presence check.

    Returns ``(matched, evidence_list)``.
    """
    signal = cond["signal"]
    expected = cond.get("exists", True)

    present = signal in stats.stats
    matched = present == expected
    evidence = [f"{signal}_present={present}"]
    return matched, evidence


# ---------------------------------------------------------------------------
# Rule evaluation
# ---------------------------------------------------------------------------


def _evaluate_rule(
    rule: Dict[str, Any],
    stats: SignalStatistics,
    anomalies: AnomalyReport,
    dtc_codes: List[str],
    ctx: _TemplateContext,
) -> Optional[DiagnosticClue]:
    """Evaluate a single rule against data.  Returns a clue if ALL conditions match."""
    all_evidence: List[str] = []
    anomaly_count = 0
    matched_dtcs = ""

    for cond in rule["conditions"]:
        ctype = cond["type"]

        if ctype == "stat_check":
            matched, evidence = _eval_stat_check(cond, stats)
        elif ctype == "anomaly_check":
            matched, evidence, anomaly_count = _eval_anomaly_check(cond, anomalies)
        elif ctype == "dtc_check":
            matched, evidence, matched_dtcs = _eval_dtc_check(cond, dtc_codes)
        elif ctype == "stat_compare":
            matched, evidence = _eval_stat_compare(cond, stats)
        elif ctype == "signal_exists":
            matched, evidence = _eval_signal_exists(cond, stats)
        else:
            logger.warning("Unknown condition type '%s' in rule %s", ctype, rule["id"])
            return None

        if not matched:
            return None

        all_evidence.extend(evidence)

    # All conditions matched — populate template
    # Update context with per-rule dynamic values
    ctx["anomaly_count"] = anomaly_count
    ctx["matched_dtcs"] = matched_dtcs

    try:
        clue_text = rule["template"].format_map(ctx)
    except (KeyError, AttributeError, IndexError) as exc:
        logger.warning(
            "Template formatting failed for rule %s: %s", rule["id"], exc
        )
        clue_text = rule.get("description", rule["id"])

    return DiagnosticClue(
        rule_id=rule["id"],
        category=rule["category"],
        clue=clue_text,
        evidence=tuple(all_evidence),
        severity=rule["severity"],
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_clues(
    stats: SignalStatistics,
    anomalies: AnomalyReport,
    *,
    rules: Optional[List[Dict[str, Any]]] = None,
    rules_path: Optional[Path] = None,
) -> DiagnosticClueReport:
    """Generate diagnostic clues from statistics and anomaly data.

    Parameters
    ----------
    stats :
        Output of :func:`~obd_agent.statistics_extractor.extract_statistics`.
    anomalies :
        Output of :func:`~obd_agent.anomaly_detector.detect_anomalies`.
    rules :
        Optional list of rule dicts (for testing).  If ``None``, rules are
        loaded from *rules_path*.
    rules_path :
        Path to YAML rules file.  Defaults to the bundled
        ``diagnostic_rules.yaml``.

    Returns
    -------
    DiagnosticClueReport
        Frozen dataclass with matched clues and session metadata.
    """
    if rules is None:
        rules = _load_rules(rules_path)

    base_ctx = _build_template_context(stats)
    dtc_codes = list(stats.dtc_codes)

    matched_clues: List[DiagnosticClue] = []
    for rule in rules:
        rule_ctx = _TemplateContext(base_ctx)  # fresh copy per rule
        clue = _evaluate_rule(rule, stats, anomalies, dtc_codes, rule_ctx)
        if clue is not None:
            matched_clues.append(clue)

    return DiagnosticClueReport(
        clues=tuple(matched_clues),
        vehicle_id=stats.vehicle_id,
        time_range=stats.time_range,
        dtc_codes=tuple(dtc_codes),
        rules_applied=len(rules),
        rules_matched=len(matched_clues),
    )


def generate_clues_from_log_file(
    path: str | Path,
    *,
    interval_seconds: float = 1.0,
    fill_method: FillMethod = "interpolate",
    vehicle_id: Optional[str] = None,
    rules: Optional[List[Dict[str, Any]]] = None,
    rules_path: Optional[Path] = None,
) -> DiagnosticClueReport:
    """Parse an OBD log file and generate diagnostic clues.

    Convenience wrapper that chains normalize → statistics → anomalies → clues.

    Parameters
    ----------
    path :
        Path to a raw OBD TSV log file.
    interval_seconds :
        Desired uniform grid spacing (default ``1.0`` s).
    fill_method :
        Gap-filling strategy.
    vehicle_id :
        Override vehicle ID.
    rules :
        Optional list of rule dicts (for testing).
    rules_path :
        Path to YAML rules file.
    """
    ts = normalize_log_file(
        path,
        interval_seconds=interval_seconds,
        fill_method=fill_method,
        vehicle_id=vehicle_id,
    )
    sig_stats = extract_statistics(ts)
    anomaly_report = detect_anomalies(ts)

    return generate_clues(
        sig_stats,
        anomaly_report,
        rules=rules,
        rules_path=rules_path,
    )
