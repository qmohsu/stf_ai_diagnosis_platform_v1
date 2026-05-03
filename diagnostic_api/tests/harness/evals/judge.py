"""LLM-as-judge for the comparative manual-eval suite.

In the HARNESS-15 / Issue #74 redesign the judge is responsible
only for the subjective ``answer_quality`` rating; the rest of
the rubric is computed deterministically in
``tests.harness.evals.metrics``.  This file provides:

- ``rate_answer_quality(entry, run)`` — calls ``z-ai/glm-5.1``
  via OpenRouter and returns ``(answer_quality, reasoning)``.
  Single-retry policy on parse / API failure; falls back to
  ``(0.0, "[judge failure] ...")`` rather than raising.
- ``grade_run(entry, run)`` — orchestrator that combines the
  deterministic metrics + the judge's answer_quality into a
  final ``Grade``.

Author: Li-Ta Hsu
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

import structlog
from openai import AsyncOpenAI
from pydantic import ValidationError

from app.config import settings
from tests.harness.evals.judge_prompts import (
    JUDGE_SYSTEM_PROMPT,
    build_user_prompt,
)
from tests.harness.evals.metrics import (
    DEFAULT_OVERALL_WEIGHTS,
    DeterministicMetrics,
    compute_deterministic_metrics,
    compute_overall,
)
from tests.harness.evals.schemas import (
    GoldenEntry,
    Grade,
    SystemRunResult,
)

logger = structlog.get_logger(__name__)


# ── Constants ─────────────────────────────────────────────────────


_JUDGE_MODEL = "z-ai/glm-5.1"
"""Model identifier on OpenRouter.  Pinned; comparability across
runs depends on a stable judge.  We learned during dev that GLM
5.1 occasionally returns empty content on heavy Chinese inputs;
``deepseek/deepseek-v4-pro`` is a reasonable fallback the eval
driver can swap in via the module-constant override pattern."""


_JUDGE_TEMPERATURE = 0.0
"""Determinism setting.  Subjective rating still benefits from
temperature 0 — same input → same score across runs."""


_JUDGE_MAX_TOKENS = 512
"""Cap on the judge's response length.  The new prompt wants
only ``{"answer_quality": ..., "reasoning": "..."}`` — well
under 256 tokens.  Extra headroom absorbs occasional
verbosity."""


_MAX_ERROR_LEN = 200
"""Cap for sanitised error messages surfaced in fallback
``reasoning``.  Internal tracebacks are logged separately."""


# ── Client factory ────────────────────────────────────────────────


_cached_client: Optional[AsyncOpenAI] = None


def _build_default_client() -> AsyncOpenAI:
    """Construct a judge OpenAI client from ``settings``.

    Reads ``premium_llm_api_key`` and ``premium_llm_base_url``
    (the same env vars that drive the user-facing premium
    client).  Raises if no API key is configured so eval runs
    fail fast with a clear message.

    Returns:
        ``AsyncOpenAI`` instance configured for OpenRouter.

    Raises:
        RuntimeError: If ``PREMIUM_LLM_API_KEY`` is empty.
    """
    if not settings.premium_llm_api_key:
        raise RuntimeError(
            "Judge requires PREMIUM_LLM_API_KEY in environment.  "
            "Export it and retry, or pass --mock-judge to skip "
            "live grading.",
        )
    return AsyncOpenAI(
        api_key=settings.premium_llm_api_key,
        base_url=settings.premium_llm_base_url,
        timeout=180.0,
        default_headers={
            "HTTP-Referer": "https://stf-diagnosis.dev",
            "X-Title": "STF eval judge",
        },
    )


def _get_default_client() -> AsyncOpenAI:
    """Return the per-process cached judge client."""
    global _cached_client
    if _cached_client is None:
        _cached_client = _build_default_client()
    return _cached_client


# ── Parse helpers ─────────────────────────────────────────────────


class _AnswerQualityPayload:
    """Lightweight wrapper around the judge's parsed JSON.

    Not a Pydantic model because Pydantic would validate during
    parsing and we want the raw error path to surface specific
    field problems.  Two attributes: ``answer_quality`` (float
    in [0, 1]) and ``reasoning`` (string).
    """

    def __init__(self, answer_quality: float, reasoning: str):
        self.answer_quality = answer_quality
        self.reasoning = reasoning


def _parse_judge_payload(raw: str) -> _AnswerQualityPayload:
    """Parse the judge response into an answer-quality payload.

    Tolerant of (rare) markdown fences around the JSON.

    Args:
        raw: Raw ``content`` from the judge's chat completion.

    Returns:
        ``_AnswerQualityPayload``.

    Raises:
        ValueError: If parsing or shape validation fails.
    """
    text = (raw or "").strip()
    # Strip a markdown fence if the judge slipped one in.
    if text.startswith("```"):
        first_nl = text.find("\n")
        last_fence = text.rfind("```")
        if first_nl != -1 and last_fence > first_nl:
            text = text[first_nl + 1:last_fence].strip()

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"judge response not valid JSON: {exc}",
        ) from exc
    if not isinstance(payload, dict):
        raise ValueError(
            f"judge response not a JSON object: {type(payload)}",
        )

    aq = payload.get("answer_quality")
    if aq is None:
        raise ValueError(
            "judge response missing 'answer_quality'",
        )
    try:
        aq_float = float(aq)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"answer_quality not coercible to float: {aq!r}",
        ) from exc
    if not (0.0 <= aq_float <= 1.0):
        raise ValueError(
            f"answer_quality out of [0,1]: {aq_float}",
        )

    reasoning = payload.get("reasoning", "")
    if not isinstance(reasoning, str):
        reasoning = str(reasoning)

    return _AnswerQualityPayload(
        answer_quality=aq_float,
        reasoning=reasoning,
    )


# ── Judge call ────────────────────────────────────────────────────


def _build_messages(
    entry: GoldenEntry, run: SystemRunResult,
) -> List[Dict[str, str]]:
    """Assemble system+user messages for one grading call."""
    return [
        {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": build_user_prompt(entry, run),
        },
    ]


async def _call_judge(
    client: AsyncOpenAI,
    messages: List[Dict[str, str]],
) -> str:
    """Invoke the judge model; return raw content.

    Raises:
        RuntimeError: If the response has no choices or no
            content.  Network / API exceptions propagate.
    """
    completion = await client.chat.completions.create(
        model=_JUDGE_MODEL,
        messages=messages,
        temperature=_JUDGE_TEMPERATURE,
        max_tokens=_JUDGE_MAX_TOKENS,
        response_format={"type": "json_object"},
    )
    if not completion.choices:
        raise RuntimeError("judge returned no choices")
    content = completion.choices[0].message.content
    if not content:
        raise RuntimeError("judge returned empty content")
    return content


# ── Public: rate answer quality (LLM call) ───────────────────────


async def rate_answer_quality(
    entry: GoldenEntry,
    run: SystemRunResult,
    client: Optional[AsyncOpenAI] = None,
) -> Tuple[float, str]:
    """Get ``(answer_quality, reasoning)`` from the judge.

    Single-retry policy: on first-try parse or API failure,
    re-prompt with a corrective nudge.  On second failure
    return ``(0.0, "[judge failure] ...")`` so the eval keeps
    running.

    Args:
        entry: Golden reference.
        run: System output to grade.
        client: Optional pre-built client (tests use a fake;
            production lazily builds from settings).

    Returns:
        ``(answer_quality, reasoning)``.
    """
    eff_client = client or _get_default_client()
    messages = _build_messages(entry, run)

    logger.info(
        "judge_invoke",
        entry_id=entry.id,
        system=run.system_label,
        model=_JUDGE_MODEL,
    )

    # ── First attempt ───────────────────────────────────────────
    try:
        raw = await _call_judge(eff_client, messages)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "judge_api_error_first_try",
            entry_id=entry.id,
            system=run.system_label,
            exc_info=exc,
        )
        # Retry once on transient API errors.
        try:
            raw = await _call_judge(eff_client, messages)
        except Exception as exc2:  # noqa: BLE001
            logger.error(
                "judge_api_error_retry",
                entry_id=entry.id,
                system=run.system_label,
                exc_info=exc2,
            )
            return 0.0, _sanitise_error(
                f"api error: {type(exc2).__name__}",
            )

    try:
        payload = _parse_judge_payload(raw)
        return payload.answer_quality, payload.reasoning
    except ValueError as parse_exc:
        logger.warning(
            "judge_parse_error_first_try",
            entry_id=entry.id,
            error=str(parse_exc),
            raw_preview=raw[:200],
        )

    # ── Retry with corrective nudge ─────────────────────────────
    retry_messages = list(messages)
    retry_messages.append({"role": "assistant", "content": raw})
    retry_messages.append({
        "role": "user",
        "content": (
            "Your previous response could not be parsed.  "
            'Return ONLY {"answer_quality": <float 0.0-1.0>, '
            '"reasoning": "<2-4 sentences>"} — no markdown '
            "fences, no extra prose."
        ),
    })

    try:
        raw_retry = await _call_judge(eff_client, retry_messages)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "judge_api_error_on_retry_call",
            entry_id=entry.id,
            exc_info=exc,
        )
        return 0.0, _sanitise_error(
            f"api error on retry: {type(exc).__name__}",
        )

    try:
        payload = _parse_judge_payload(raw_retry)
        return payload.answer_quality, payload.reasoning
    except ValueError as parse_exc2:
        logger.error(
            "judge_parse_error_retry",
            entry_id=entry.id,
            error=str(parse_exc2),
            raw_preview=raw_retry[:200],
        )
        return 0.0, _sanitise_error(
            f"parse error after retry: {parse_exc2}",
        )


def _sanitise_error(reason: str) -> str:
    """Trim error reason to a safe length for the report."""
    if len(reason) > _MAX_ERROR_LEN:
        reason = reason[:_MAX_ERROR_LEN] + "..."
    return f"[judge failure] {reason}"


# ── Public: grade_run — full pipeline ────────────────────────────


async def grade_run(
    entry: GoldenEntry,
    run: SystemRunResult,
    client: Optional[AsyncOpenAI] = None,
    weights: Optional[Dict[str, float]] = None,
) -> Grade:
    """End-to-end grading: deterministic metrics + judge + combine.

    Use this from the eval driver — it's the single function
    that takes a golden + system run and produces a complete
    ``Grade``.

    Args:
        entry: Golden reference.
        run: System output (agent or RAG, both via
            ``SystemRunResult``).
        client: Optional pre-built judge client.
        weights: Optional override for the ``overall`` formula
            weights.  Defaults to
            ``metrics.DEFAULT_OVERALL_WEIGHTS``.

    Returns:
        Validated ``Grade``.
    """
    # Step 1: deterministic metrics (no LLM call).
    det = compute_deterministic_metrics(entry, run)

    # Step 2: ask the judge for answer_quality.
    answer_quality, reasoning = await rate_answer_quality(
        entry, run, client=client,
    )

    # Step 3: combine into overall.
    overall = compute_overall(det, answer_quality, weights=weights)

    # Step 4: enrich reasoning with deterministic-metric
    # diagnostics so the report explains both numeric and
    # judge-derived signals in one place.
    enriched_reasoning = _build_enriched_reasoning(
        det, answer_quality, reasoning,
    )

    return Grade(
        section_recall=det.section_recall,
        section_precision=det.section_precision,
        fact_recall=det.fact_recall,
        fact_density=det.fact_density,
        hallucination_penalty=det.hallucination_penalty,
        citation_quality=det.citation_quality,
        answer_quality=answer_quality,
        trajectory_efficiency=det.trajectory_efficiency,
        overall=overall,
        reasoning=enriched_reasoning,
    )


def _build_enriched_reasoning(
    det: DeterministicMetrics,
    answer_quality: float,
    judge_reasoning: str,
) -> str:
    """Compose a human-readable reasoning block for the report.

    Combines deterministic-metric diagnostics with the judge's
    free-text answer_quality reasoning so both signal sources
    are surfaced in one place.
    """
    lines = []
    if det.fact_recall_misses:
        misses_preview = ", ".join(
            f"'{m}'" for m in det.fact_recall_misses[:3]
        )
        if len(det.fact_recall_misses) > 3:
            misses_preview += f", +{len(det.fact_recall_misses) - 3} more"
        lines.append(
            f"Missing must_contain: {misses_preview}.",
        )
    if det.hallucination_hits:
        hall_preview = ", ".join(
            f"'{h}'" for h in det.hallucination_hits
        )
        lines.append(f"Hallucinated terms: {hall_preview}.")
    lines.append(
        f"Judge ({answer_quality:.2f}): {judge_reasoning}",
    )
    return " ".join(lines)


# ── Test-only helpers ────────────────────────────────────────────


def _reset_client_cache_for_testing() -> None:
    """Drop the per-process client cache.

    Tests that swap env vars between cases must call this so a
    previously-built cached client doesn't leak.
    """
    global _cached_client
    _cached_client = None
