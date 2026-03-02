"""Translation service for converting Chinese manual text to English.

Uses a local Ollama LLM (e.g., llama3:8b) to translate Traditional
Chinese section text to English at ingestion time, so the entire
Weaviate vector store is uniform English.  Follows the same service
pattern as embedding.py and vision.py (singleton httpx.AsyncClient,
structlog).
"""

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

_TRANSLATION_PROMPT = (
    "Translate the following automotive service manual text "
    "from Chinese to English.\n"
    "Rules:\n"
    "- Preserve all part numbers, model numbers, and codes "
    "exactly as-is\n"
    "- Preserve all measurement values (torque specs, "
    "dimensions) exactly as-is\n"
    "- Use standard automotive terminology\n"
    "- Translate accurately and completely; do not summarize "
    "or omit content\n"
    "- Output ONLY the English translation, no explanations\n"
    "\nChinese text:\n"
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
            self._client = httpx.AsyncClient(timeout=120.0)
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
        prompt = _TRANSLATION_PROMPT + sanitized

        try:
            response = await client.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                },
            )
            response.raise_for_status()
            result = response.json()
            translated = result.get("response", "").strip()

            if not translated:
                logger.warning(
                    "translation_service.empty_response",
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
    ) -> List[Section]:
        """Translate a list of Sections to English.

        Args:
            sections: Sections with potentially Chinese content.

        Returns:
            List of Sections with English content.
        """
        # TODO(18): When multi-GPU / vLLM is available, translate
        # sections concurrently with asyncio.Semaphore, similar
        # to TODO(15) in ingest.py for file processing.
        translated: List[Section] = []
        total = len(sections)
        skipped = 0
        failed = 0

        for idx, section in enumerate(sections):
            if not has_cjk(section.body) and not has_cjk(
                section.title
            ):
                translated.append(section)
                skipped += 1
                continue

            result = await self.translate_section(section)
            translated.append(result)

            # Detect fallback: body still contains CJK after
            # translation attempt → count as failure.
            if has_cjk(result.body) and has_cjk(section.body):
                failed += 1

            if (idx + 1) % 50 == 0 or idx + 1 == total:
                logger.info(
                    "translation_service.progress",
                    done=idx + 1,
                    total=total,
                    skipped=skipped,
                    failed=failed,
                )

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
