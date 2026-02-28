"""Premium LLM client using Anthropic Claude for cloud-based diagnosis.

This client is feature-gated by ``PREMIUM_LLM_ENABLED`` and is the only
component in the platform that requires internet access.

Author: Li-Ta Hsu
Date: February 2026
"""

from typing import AsyncIterator

import structlog
from anthropic import AsyncAnthropic

from app.expert import prompts

logger = structlog.get_logger()


class PremiumLLMClient:
    """Client for interacting with Anthropic Claude (cloud API).

    Uses the same prompts and message structure as the local
    ``ExpertLLMClient`` so that diagnosis outputs are directly
    comparable.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "claude-opus-4-6",
    ) -> None:
        if not api_key:
            logger.warning("premium_llm_client_no_api_key")
        self._has_api_key = bool(api_key)
        self.model = model
        self.client = AsyncAnthropic(api_key=api_key)
        logger.info(
            "initialized_premium_llm_client",
            model=self.model,
        )

    def _build_obd_diagnosis_messages(
        self,
        parsed_summary: dict,
        context: str,
    ) -> tuple[str, list[dict]]:
        """Build system prompt and message list for OBD diagnosis.

        Returns:
            Tuple of (system_prompt, messages) where messages is a
            list of dicts with ``role`` and ``content`` keys.
        """
        user_prompt = prompts.OBD_DIAGNOSIS_USER_TEMPLATE.format(
            vehicle_id=parsed_summary.get("vehicle_id", "Unknown"),
            time_range=parsed_summary.get("time_range", "Unknown"),
            dtc_codes=parsed_summary.get("dtc_codes", "None"),
            pid_summary=parsed_summary.get("pid_summary", "N/A"),
            anomaly_events=parsed_summary.get(
                "anomaly_events", "None"
            ),
            diagnostic_clues=parsed_summary.get(
                "diagnostic_clues", "None"
            ),
            context=context or "No additional context retrieved.",
        )
        return (
            prompts.OBD_DIAGNOSIS_SYSTEM_PROMPT,
            [{"role": "user", "content": user_prompt}],
        )

    async def generate_obd_diagnosis_stream(
        self,
        parsed_summary: dict,
        context: str,
    ) -> AsyncIterator[str]:
        """Stream OBD diagnosis token-by-token via Anthropic API.

        Args:
            parsed_summary: Flat-string parsed summary dict.
            context: RAG-retrieved context string.

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

        system_prompt, messages = self._build_obd_diagnosis_messages(
            parsed_summary, context
        )
        logger.info(
            "premium_obd_diagnosis_stream_start",
            vehicle_id=parsed_summary.get("vehicle_id"),
            model=self.model,
        )

        try:
            async with self.client.messages.stream(
                model=self.model,
                max_tokens=8192,
                temperature=0.3,
                system=system_prompt,
                messages=messages,
            ) as stream:
                async for text in stream.text_stream:
                    yield text

        except Exception as exc:
            # Avoid logging the API key if it appears in the
            # exception message (e.g. authentication errors).
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
