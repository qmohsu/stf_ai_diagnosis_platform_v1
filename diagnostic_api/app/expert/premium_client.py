"""Premium LLM client using OpenRouter for cloud-based diagnosis.

This client is feature-gated by ``PREMIUM_LLM_ENABLED`` and is the only
component in the platform that requires internet access.  It connects
to OpenRouter's OpenAI-compatible API, giving access to models from
Anthropic, OpenAI, Google, Meta, and others through a single endpoint.

Author: Li-Ta Hsu
Date: February 2026
"""

from typing import AsyncIterator, Optional

import structlog
from openai import AsyncOpenAI

from app.expert import prompts

logger = structlog.get_logger()


class PremiumLLMClient:
    """Client for interacting with OpenRouter (OpenAI-compatible API).

    Uses the same prompts and message structure as the local
    ``ExpertLLMClient`` so that diagnosis outputs are directly
    comparable across providers and models.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://openrouter.ai/api/v1",
        model: str = "anthropic/claude-sonnet-4",
    ) -> None:
        if not api_key:
            logger.warning("premium_llm_client_no_api_key")
        self._has_api_key = bool(api_key)
        self.model = model
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            default_headers={
                "HTTP-Referer": "https://stf-diagnosis.local",
                "X-Title": "STF AI Diagnosis Platform",
            },
        )
        logger.info(
            "initialized_premium_llm_client",
            base_url=base_url,
            model=self.model,
        )

    def _build_obd_diagnosis_messages(
        self,
        parsed_summary: dict,
        context: str,
    ) -> list[dict]:
        """Build message list for OBD diagnosis.

        Returns:
            List of dicts with ``role`` and ``content`` keys,
            matching the OpenAI chat completions format.
        """
        user_prompt = prompts.OBD_DIAGNOSIS_USER_TEMPLATE.format(
            vehicle_id=parsed_summary.get(
                "vehicle_id", "Unknown"
            ),
            time_range=parsed_summary.get(
                "time_range", "Unknown"
            ),
            dtc_codes=parsed_summary.get("dtc_codes", "None"),
            pid_summary=parsed_summary.get(
                "pid_summary", "N/A"
            ),
            anomaly_events=parsed_summary.get(
                "anomaly_events", "None"
            ),
            diagnostic_clues=parsed_summary.get(
                "diagnostic_clues", "None"
            ),
            context=context
            or "No additional context retrieved.",
        )
        return [
            {
                "role": "system",
                "content": prompts.OBD_DIAGNOSIS_SYSTEM_PROMPT,
            },
            {"role": "user", "content": user_prompt},
        ]

    async def generate_obd_diagnosis_stream(
        self,
        parsed_summary: dict,
        context: str,
        model_override: Optional[str] = None,
    ) -> AsyncIterator[str]:
        """Stream OBD diagnosis token-by-token via OpenRouter.

        Args:
            parsed_summary: Flat-string parsed summary dict.
            context: RAG-retrieved context string.
            model_override: If provided, use this model instead
                of the default configured model.

        Yields:
            Text chunks as the LLM generates them.

        Raises:
            ValueError: If api_key is empty.
        """
        if not self._has_api_key:
            raise ValueError(
                "Premium LLM API key is not configured. "
                "Set PREMIUM_LLM_API_KEY in environment."
            )

        effective_model = model_override or self.model
        messages = self._build_obd_diagnosis_messages(
            parsed_summary, context
        )
        logger.info(
            "premium_obd_diagnosis_stream_start",
            vehicle_id=parsed_summary.get("vehicle_id"),
            model=effective_model,
        )

        try:
            stream = await self.client.chat.completions.create(
                model=effective_model,
                messages=messages,
                temperature=0.3,
                max_tokens=8192,
                stream=True,
            )

            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    yield delta.content

        except Exception as exc:
            error_msg = str(exc)
            api_key = self.client.api_key or ""
            if api_key and api_key in error_msg:
                error_msg = error_msg.replace(
                    api_key, "***REDACTED***"
                )
            logger.error(
                "premium_obd_diagnosis_stream_failed",
                error=error_msg,
            )
            raise
