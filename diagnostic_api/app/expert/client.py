"""Local Expert LLM client for on-premise diagnosis via Ollama.

Connects to an Ollama instance via its OpenAI-compatible API endpoint.
Only streaming diagnosis generation is supported.

Author: Li-Ta Hsu
Date: January 2026
"""

import os
import httpx
import structlog
from typing import AsyncIterator, Optional
from openai import AsyncOpenAI
from app.config import settings
from app.expert import prompts

logger = structlog.get_logger()

# Sentinel yielded during the LLM thinking phase so the SSE layer
# can send keep-alive comments and prevent proxy idle timeouts.
THINKING_SENTINEL = "\x00__THINKING__\x00"


async def prewarm_local_model(
    endpoint: Optional[str] = None,
    model: Optional[str] = None,
    keep_alive: str = "-1",
    timeout: float = 600.0,
) -> bool:
    """Pre-load the local Ollama model so it is resident.

    Sends a minimal generation request to Ollama's native
    ``/api/generate`` endpoint with ``keep_alive`` set so the model
    is loaded into VRAM and stays resident.  This is intended to run
    once at API startup: after a deploy recreates the Ollama
    container, the model is evicted, and the first user diagnosis
    would otherwise trigger a multi-minute cold load that silently
    exceeds the SSE idle timeout in the browser → Cloudflare → Nginx
    chain (Issue #128).  Warming up here means the model is already
    loading (or loaded) before anyone clicks "diagnose".

    Failures are non-fatal — the timer-based SSE keep-alive
    (``_with_keepalive``) is the backstop that lets a cold load
    complete even if this warm-up is skipped or fails.

    Args:
        endpoint: Ollama base URL.  Defaults to ``settings.llm_endpoint``.
        model: Model tag to load.  Defaults to ``settings.llm_model``.
        keep_alive: Ollama ``keep_alive`` value (``"-1"`` = never
            unload).  Passed through verbatim.
        timeout: Request timeout in seconds.  Generous, because a
            cold load on a shared GPU can take minutes.

    Returns:
        True if the warm-up request succeeded, False otherwise.
    """
    endpoint = (endpoint or settings.llm_endpoint).rstrip("/")
    model = model or settings.llm_model
    url = f"{endpoint}/api/generate"
    payload = {
        "model": model,
        "prompt": "ping",
        "stream": False,
        "keep_alive": keep_alive,
        "options": {"num_predict": 1},
    }
    logger.info(
        "llm_prewarm_start", model=model, endpoint=endpoint,
    )
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
        logger.info(
            "llm_prewarm_complete", model=model, endpoint=endpoint,
        )
        return True
    except Exception as exc:
        logger.warning(
            "llm_prewarm_failed",
            model=model,
            endpoint=endpoint,
            error=str(exc),
        )
        return False


class ExpertLLMClient:
    """
    Client for interacting with the Expert Diagnostic Model (hosted on Ollama).
    """

    def __init__(self, base_url: Optional[str] = None, model: Optional[str] = None):
        self.base_url = base_url or f"{settings.llm_endpoint}/v1"
        self.model = model or settings.llm_model
        self.client = AsyncOpenAI(
            api_key="ollama",
            base_url=self.base_url,
            timeout=300.0,
        )
        logger.info("initialized_expert_llm_client", base_url=self.base_url, model=self.model)

    def _build_obd_diagnosis_messages(
        self,
        parsed_summary: dict,
        context: str,
        locale: str = "en",
    ) -> list[dict]:
        """Build the message list for OBD diagnosis prompts."""
        user_prompt = prompts.OBD_DIAGNOSIS_USER_TEMPLATE.format(
            vehicle_id=parsed_summary.get("vehicle_id", "Unknown"),
            time_range=parsed_summary.get("time_range", "Unknown"),
            dtc_codes=parsed_summary.get("dtc_codes", "None"),
            pid_summary=parsed_summary.get("pid_summary", "N/A"),
            anomaly_events=parsed_summary.get("anomaly_events", "None"),
            diagnostic_clues=parsed_summary.get("diagnostic_clues", "None"),
            context=context or "No additional context retrieved.",
            language_instruction=prompts.get_language_instruction(locale),
        )
        return [
            {"role": "system", "content": prompts.OBD_DIAGNOSIS_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

    async def generate_obd_diagnosis_stream(
        self,
        parsed_summary: dict,
        context: str,
        locale: str = "en",
    ) -> AsyncIterator[str]:
        """Stream OBD diagnosis token-by-token.

        Yields:
            Content text chunks, or ``THINKING_SENTINEL`` during
            the model's internal reasoning phase.
        """
        messages = self._build_obd_diagnosis_messages(
            parsed_summary, context, locale
        )
        logger.info("obd_diagnosis_stream_start", vehicle_id=parsed_summary.get("vehicle_id"))

        try:
            stream = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.3,
                stream=True,
            )

            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    yield delta.content
                elif (
                    hasattr(delta, "model_extra")
                    and delta.model_extra
                    and delta.model_extra.get("reasoning")
                ):
                    yield THINKING_SENTINEL

        except Exception as e:
            logger.error("obd_diagnosis_stream_failed", error=str(e))
            raise
