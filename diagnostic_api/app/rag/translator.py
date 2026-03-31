"""Translation service for converting Chinese manual text to English.

Uses a local Ollama LLM (e.g., qwen3.5:122b-a10b) to translate Traditional
Chinese section text to English at ingestion time, so the entire
pgvector store is uniform English.  Uses the Ollama ``/api/chat``
endpoint with ``think: false`` to disable hidden reasoning tokens in
Qwen3 thinking models.  Follows the same service pattern as embedding.py
and vision.py (singleton httpx.AsyncClient, structlog).
"""

import asyncio
import re
from typing import List

import httpx
import structlog

from app.config import settings
from app.rag.cjk_utils import (
    IMAGE_MARKER_SPLIT,
    has_cjk,
    count_cjk,
)
from app.rag.parser import Section

logger = structlog.get_logger(__name__)

_SYSTEM_PROMPT = (
    "You are a professional automotive translator. "
    "Translate Chinese service manual text to English. "
    "Rules: preserve all part numbers, model numbers, codes, "
    "and measurement values exactly as-is. "
    "Use standard automotive terminology. "
    "Translate accurately and completely; do not summarize. "
    "Output ONLY the English translation."
)

_USER_PROMPT_PREFIX = "Chinese text:\n"

# Extract content after the last </think> tag.  Qwen3.5 models
# may generate thinking blocks even with /no_think; the actual
# translation appears *after* the closing tag.
_THINK_CLOSE_RE = re.compile(
    r"</think>\s*", re.DOTALL
)

# Skip translation for very short text (likely just a code)
_MIN_CJK_CHARS = 4

# Guard against extremely long sections that could OOM the LLM.
# Sections longer than this are skipped (kept in original Chinese).
_MAX_TRANSLATE_CHARS = 8000


class TranslationService:
    """Client for translating Chinese text to English via Ollama.

    Reuses a single ``httpx.AsyncClient`` for connection pooling.
    Call :meth:`close` when the service is no longer needed.
    """

    def __init__(self) -> None:
        self.base_url = settings.llm_endpoint
        self.model = settings.llm_model
        self._client: httpx.AsyncClient | None = None
        logger.info(
            "translation_service.init",
            model=self.model,
            endpoint=self.base_url,
        )

    async def _get_client(self) -> httpx.AsyncClient:
        """Return a reusable async HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=300.0)
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    async def translate_text(self, text: str) -> str:
        """Translate Chinese text to English via Ollama.

        Args:
            text: Chinese text to translate.

        Returns:
            English translation, or original text on failure.
        """
        if not text or not text.strip():
            return text

        if not has_cjk(text) or count_cjk(text) < _MIN_CJK_CHARS:
            return text

        # Sanitize: strip null bytes from PDF extraction
        sanitized = text.replace("\x00", "")

        # Guard against oversized input that could OOM the LLM
        if len(sanitized) > _MAX_TRANSLATE_CHARS:
            logger.warning(
                "translation_service.text_too_long",
                text_len=len(sanitized),
                max_len=_MAX_TRANSLATE_CHARS,
            )
            return text

        client = await self._get_client()

        try:
            response = await client.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "messages": [
                        {
                            "role": "system",
                            "content": _SYSTEM_PROMPT,
                        },
                        {
                            "role": "user",
                            "content": (
                                _USER_PROMPT_PREFIX + sanitized
                            ),
                        },
                    ],
                    "stream": False,
                    "think": False,
                },
            )
            response.raise_for_status()
            result = response.json()
            msg = result.get("message", {})
            raw = msg.get("content", "")
            logger.debug(
                "translation_service.raw_response",
                raw_len=len(raw),
                eval_count=result.get("eval_count", 0),
                raw_head=raw[:200] if raw else "(empty)",
                text_len=len(sanitized),
            )
            # Extract content after </think> tag if present
            # (safety net in case think: false is ignored).
            # re.split always returns a non-empty list; if no
            # </think> tag exists, parts == [raw].
            parts = _THINK_CLOSE_RE.split(raw)
            translated = parts[-1].strip()

            if not translated:
                logger.warning(
                    "translation_service.empty_response",
                    raw_len=len(raw),
                    text_len=len(sanitized),
                )
                return text

            return translated

        except httpx.TimeoutException:
            logger.warning(
                "translation_service.timeout",
                text_len=len(sanitized),
            )
            return text
        except httpx.HTTPStatusError as e:
            logger.error(
                "translation_service.http_error",
                status_code=e.response.status_code,
                text_len=len(sanitized),
            )
            return text
        except Exception as e:
            logger.error(
                "translation_service.unexpected_error",
                error_type=type(e).__name__,
                error=str(e),
                text_len=len(sanitized),
            )
            return text

    async def translate_section(
        self,
        section: Section,
    ) -> Section:
        """Translate a Section's title and body to English.

        Preserves image/OCR marker blocks (already English) and
        only translates CJK text portions.

        Args:
            section: Section with potentially Chinese content.

        Returns:
            New Section with English title and body.
        """
        body = section.body
        title = section.title

        # Translate title if it contains CJK
        if has_cjk(title):
            title = await self.translate_text(title)

        if not has_cjk(body):
            if title != section.title:
                return Section(
                    title=title,
                    level=section.level,
                    body=body,
                    vehicle_model=section.vehicle_model,
                    dtc_codes=section.dtc_codes,
                )
            return section

        # Split body into translatable blocks and marker blocks.
        # Marker blocks (e.g., [Image 1, Page 5]\nDescription: ...)
        # are already English and should be preserved as-is.
        parts = IMAGE_MARKER_SPLIT.split(body)
        translated_parts: List[str] = []

        for part in parts:
            if not part.strip():
                translated_parts.append(part)
                continue

            if IMAGE_MARKER_SPLIT.match(part):
                translated_parts.append(part)
            elif has_cjk(part):
                translated_parts.append(
                    await self.translate_text(part)
                )
            else:
                translated_parts.append(part)

        translated_body = "".join(translated_parts)

        return Section(
            title=title,
            level=section.level,
            body=translated_body,
            vehicle_model=section.vehicle_model,
            dtc_codes=section.dtc_codes,
        )

    async def translate_sections(
        self,
        sections: List[Section],
        *,
        max_concurrent: int = 2,
    ) -> List[Section]:
        """Translate a list of Sections to English.

        Uses bounded concurrency (``asyncio.Semaphore``) to
        translate multiple sections in parallel while respecting
        GPU memory limits.

        Args:
            sections: Sections with potentially Chinese content.
            max_concurrent: Maximum parallel translation tasks.

        Returns:
            List of Sections with English content (order preserved).
        """
        if max_concurrent < 1:
            raise ValueError(
                "max_concurrent must be >= 1, "
                f"got {max_concurrent}"
            )
        total = len(sections)
        sem = asyncio.Semaphore(max_concurrent)
        done_count = 0
        skipped = 0
        failed = 0
        # SAFETY: asyncio is single-threaded; mutations under
        # this lock are safe because no await occurs while held.
        lock = asyncio.Lock()

        async def _translate_one(
            idx: int,
            section: Section,
        ) -> tuple[int, Section]:
            nonlocal done_count, skipped, failed
            if not has_cjk(section.body) and not has_cjk(
                section.title
            ):
                async with lock:
                    skipped += 1
                    done_count += 1
                return idx, section

            async with sem:
                result = await self.translate_section(section)

            async with lock:
                done_count += 1
                if has_cjk(result.body) and has_cjk(
                    section.body
                ):
                    failed += 1
                if done_count % 20 == 0 or done_count == total:
                    logger.info(
                        "translation_service.progress",
                        done=done_count,
                        total=total,
                        skipped=skipped,
                        failed=failed,
                    )

            return idx, result

        tasks = [
            _translate_one(i, s) for i, s in enumerate(sections)
        ]
        results = await asyncio.gather(*tasks)

        # Restore original order
        ordered = sorted(results, key=lambda r: r[0])
        translated = [section for _, section in ordered]

        logger.info(
            "translation_service.complete",
            total=total,
            translated=total - skipped - failed,
            skipped=skipped,
            failed=failed,
        )
        return translated


# Lazy singleton
_translation_service: TranslationService | None = None


def get_translation_service() -> TranslationService:
    """Return the singleton TranslationService."""
    global _translation_service
    if _translation_service is None:
        _translation_service = TranslationService()
    return _translation_service
