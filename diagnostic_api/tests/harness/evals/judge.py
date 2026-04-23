"""LLM-as-judge for the manual-agent evaluation suite.

Grades a ``ManualAgentResult`` against a ``GoldenEntry`` by calling
``z-ai/glm-5.1`` via OpenRouter with ``temperature=0`` and
``response_format={"type": "json_object"}``.  The model's JSON is
validated by Pydantic; on parse or schema failure, the judge
retries exactly once with a corrective user message, then falls
back to a zero-score ``Grade`` tagged as a judge parse failure
(never crashes the eval run).

Client injection: callers may pass their own ``AsyncOpenAI``
instance for testing; otherwise a module-local client is built
lazily from ``settings`` and cached per-process.

Author: Li-Ta Hsu
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import structlog
from openai import AsyncOpenAI
from pydantic import ValidationError

from app.config import settings
from tests.harness.evals.judge_prompts import (
    JUDGE_SYSTEM_PROMPT,
    build_user_prompt,
)
from tests.harness.evals.schemas import (
    GoldenEntry,
    Grade,
    ManualAgentResult,
)

logger = structlog.get_logger(__name__)


# ── Constants ─────────────────────────────────────────────────────

_JUDGE_MODEL = "z-ai/glm-5.1"
"""Model identifier on OpenRouter.  Pinned; do not override
per call — the eval suite's comparability across runs depends
on a stable judge."""

_JUDGE_TEMPERATURE = 0.0
"""Determinism setting.  Do not raise for 'more diverse' grades
— the rubric is factual."""

_JUDGE_MAX_TOKENS = 2048
"""Cap on the judge's response length.  The rubric JSON plus a
400-char reasoning field fits comfortably under 1K tokens; the
extra headroom absorbs occasional over-generation."""

_MAX_ERROR_LEN = 200
"""Cap for sanitised error messages surfaced in the fallback
``Grade.reasoning``.  Internal tracebacks are logged separately."""


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
            "Judge requires PREMIUM_LLM_API_KEY to be set. "
            "Export it in your environment and retry, or pass "
            "a pre-built client via the `client` argument.",
        )
    return AsyncOpenAI(
        api_key=settings.premium_llm_api_key,
        base_url=settings.premium_llm_base_url,
        default_headers={
            "HTTP-Referer": "https://stf-diagnosis.local",
            "X-Title": "STF Manual-Agent Eval",
        },
    )


def _get_default_client() -> AsyncOpenAI:
    """Return a process-cached default judge client, lazy-built."""
    global _cached_client
    if _cached_client is None:
        _cached_client = _build_default_client()
    return _cached_client


# ── Prompt + parsing helpers ──────────────────────────────────────


def _build_messages(
    entry: GoldenEntry,
    result: ManualAgentResult,
) -> List[Dict[str, str]]:
    """Assemble the system+user message pair for one grading call.

    Args:
        entry: Golden reference.
        result: Agent output to score.

    Returns:
        Two-element message list.
    """
    return [
        {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": build_user_prompt(entry, result),
        },
    ]


def _parse_grade(raw: str) -> Grade:
    """Parse a judge response string into a ``Grade``.

    Args:
        raw: Raw ``content`` from the judge's chat completion.

    Returns:
        Validated ``Grade``.

    Raises:
        ValueError: If the string is not valid JSON or does not
            match the ``Grade`` schema.  The eval loop catches
            this and retries once.
    """
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"judge response not valid JSON: {exc}",
        ) from exc
    try:
        return Grade.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(
            f"judge response failed schema: {exc}",
        ) from exc


async def _call_judge(
    client: AsyncOpenAI,
    messages: List[Dict[str, str]],
) -> str:
    """Invoke the judge model and return raw content.

    Args:
        client: Configured OpenAI-compatible client.
        messages: Chat messages (system + user).

    Returns:
        The assistant's ``content`` string.

    Raises:
        RuntimeError: If the response has no content block.
        Any network / API exception from the client.
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


def _fallback_grade(reason: str) -> Grade:
    """Build a zero-score Grade used when the judge itself fails.

    Keeps the eval run alive — a single unparseable response
    shouldn't crash the entire suite.

    Args:
        reason: Short sanitised explanation for the report.

    Returns:
        Zero-score ``Grade``.
    """
    if len(reason) > _MAX_ERROR_LEN:
        reason = reason[:_MAX_ERROR_LEN] + "..."
    return Grade(
        section_match=0,
        fact_recall=0.0,
        hallucination=0,
        citation_present=0,
        trajectory_ok=0,
        overall=0.0,
        reasoning=f"[judge failure] {reason}",
    )


# ── Main entry point ──────────────────────────────────────────────


async def judge_result(
    entry: GoldenEntry,
    result: ManualAgentResult,
    client: Optional[AsyncOpenAI] = None,
) -> Grade:
    """Grade ``result`` against ``entry`` using GLM 5.1.

    Single retry policy: if the first response fails JSON parsing
    or Pydantic validation, the judge is re-prompted with a short
    corrective user message.  If the retry also fails, returns a
    zero-score ``Grade`` tagged ``[judge failure]`` rather than
    raising — keeping the eval suite running on other entries.

    Args:
        entry: Golden reference for this question.
        result: Manual agent's output to grade.
        client: Optional pre-built ``AsyncOpenAI`` instance.
            Tests pass a fake client; production omits and the
            default OpenRouter client is built from settings.

    Returns:
        Validated ``Grade``.
    """
    client = client or _get_default_client()
    messages = _build_messages(entry, result)

    logger.info(
        "judge_invoke",
        entry_id=entry.id,
        category=entry.category,
        model=_JUDGE_MODEL,
    )

    # ── First attempt ────────────────────────────────────────────
    try:
        raw = await _call_judge(client, messages)
    except Exception as exc:  # noqa: BLE001 — sanitised below.
        logger.error(
            "judge_api_error_first_try",
            entry_id=entry.id,
            exc_info=exc,
        )
        # Retry once on transient API errors too.
        try:
            raw = await _call_judge(client, messages)
        except Exception as exc2:  # noqa: BLE001
            logger.error(
                "judge_api_error_retry",
                entry_id=entry.id,
                exc_info=exc2,
            )
            return _fallback_grade(
                f"api error: {type(exc2).__name__}",
            )

    try:
        return _parse_grade(raw)
    except ValueError as parse_exc:
        logger.warning(
            "judge_parse_error_first_try",
            entry_id=entry.id,
            error=str(parse_exc),
            raw_preview=raw[:200],
        )

    # ── Retry with corrective nudge ──────────────────────────────
    retry_messages = list(messages)
    retry_messages.append({
        "role": "assistant",
        "content": raw,
    })
    retry_messages.append({
        "role": "user",
        "content": (
            "Your previous response could not be parsed as the "
            "required JSON rubric.  Return ONLY the JSON object "
            "specified in the system prompt, with all six "
            "numeric fields and the reasoning string.  No "
            "prose, no markdown fences."
        ),
    })

    try:
        raw_retry = await _call_judge(client, retry_messages)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "judge_api_error_on_retry_call",
            entry_id=entry.id,
            exc_info=exc,
        )
        return _fallback_grade(
            f"api error on retry: {type(exc).__name__}",
        )

    try:
        return _parse_grade(raw_retry)
    except ValueError as parse_exc2:
        logger.error(
            "judge_parse_error_retry",
            entry_id=entry.id,
            error=str(parse_exc2),
            raw_preview=raw_retry[:200],
        )
        return _fallback_grade(
            f"parse error after retry: {parse_exc2}",
        )


def _reset_client_cache_for_testing() -> None:
    """Reset the cached judge client.

    Test-only helper.  Unit tests that swap environment variables
    (e.g., to assert that a missing API key raises) must call
    this before each test or the cached client from a previous
    test will be reused.
    """
    global _cached_client
    _cached_client = None
