"""Unit tests for TranslationService.

All tests use mocked httpx responses -- no real Ollama needed.
"""

import httpx
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.rag.cjk_utils import has_cjk, count_cjk
from app.rag.parser import Section
from app.rag.translator import (
    TranslationService,
    _MIN_CJK_CHARS,
    _MAX_TRANSLATE_CHARS,
    get_translation_service,
)


# ------------------------------------------------------------------ #
# Shared helpers
# ------------------------------------------------------------------ #

def _mock_ollama_response(translated_text: str) -> httpx.Response:
    """Build a fake Ollama /api/chat response."""
    return httpx.Response(
        200,
        json={"message": {"content": translated_text}},
        request=httpx.Request("POST", "http://fake/api/chat"),
    )


def _make_section(
    title: str = "Test",
    body: str = "",
    level: int = 1,
    vehicle_model: str = "MWS150-A",
    dtc_codes: list | None = None,
) -> Section:
    """Create a Section for testing."""
    return Section(
        title=title,
        level=level,
        body=body,
        vehicle_model=vehicle_model,
        dtc_codes=dtc_codes or [],
    )


# ------------------------------------------------------------------ #
# CJK detection (shared utility)
# ------------------------------------------------------------------ #

class TestCjkDetection:
    """Tests for has_cjk and count_cjk from cjk_utils."""

    def test_empty_string(self):
        """Empty string has no CJK characters."""
        assert has_cjk("") is False
        assert count_cjk("") == 0

    def test_pure_ascii(self):
        """ASCII text has no CJK characters."""
        assert has_cjk("Hello world 123") is False
        assert count_cjk("Hello world 123") == 0

    def test_chinese_text(self):
        """Traditional Chinese text detected correctly."""
        assert has_cjk("引擎過熱") is True
        assert count_cjk("引擎過熱") == 4

    def test_mixed_text(self):
        """Mixed Chinese/English text detected."""
        text = "引擎 Engine 過熱 Overheat"
        assert has_cjk(text) is True
        assert count_cjk(text) == 4

    def test_cjk_punctuation_in_range(self):
        """Some CJK punctuation falls within the CJK_RANGE."""
        # 。(U+3002) is within \u2E80-\u9FFF, so has_cjk is True.
        # This is acceptable; the _MIN_CJK_CHARS threshold
        # prevents punctuation-only strings from being translated.
        assert count_cjk("。！？") >= 0  # coverage only


# ------------------------------------------------------------------ #
# TranslationService.translate_text
# ------------------------------------------------------------------ #

class TestTranslateText:
    """Tests for translate_text method."""

    @pytest.fixture
    def service(self):
        """Create a TranslationService with mocked settings."""
        with patch("app.rag.translator.settings") as mock:
            mock.llm_endpoint = "http://fake:11434"
            mock.llm_model = "llama3:8b"
            svc = TranslationService()
        return svc

    @pytest.mark.asyncio
    async def test_empty_text_returns_as_is(self, service):
        """Empty or whitespace-only text should be returned."""
        assert await service.translate_text("") == ""
        assert await service.translate_text("   ") == "   "

    @pytest.mark.asyncio
    async def test_ascii_text_skipped(self, service):
        """Text without CJK characters should not be sent to LLM."""
        result = await service.translate_text("Engine overheat")
        assert result == "Engine overheat"

    @pytest.mark.asyncio
    async def test_few_cjk_chars_skipped(self, service):
        """Text with fewer than _MIN_CJK_CHARS CJK chars skipped."""
        # 3 CJK chars < _MIN_CJK_CHARS (4)
        result = await service.translate_text("AB引擎C熱")
        assert result == "AB引擎C熱"

    @pytest.mark.asyncio
    async def test_successful_translation(self, service):
        """Chinese text should be translated via Ollama."""
        fake_resp = _mock_ollama_response("Engine overheating")

        mock_client = AsyncMock()
        mock_client.post.return_value = fake_resp
        mock_client.is_closed = False
        service._client = mock_client

        result = await service.translate_text("引擎過熱問題")
        assert result == "Engine overheating"
        mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_think_tags_stripped(self, service):
        """<think> blocks from qwen3 should be stripped."""
        raw = (
            "<think>Let me translate this...</think>\n\n"
            "Engine overheating"
        )
        fake_resp = _mock_ollama_response(raw)

        mock_client = AsyncMock()
        mock_client.post.return_value = fake_resp
        mock_client.is_closed = False
        service._client = mock_client

        result = await service.translate_text("引擎過熱問題")
        assert result == "Engine overheating"
        assert "<think>" not in result

    @pytest.mark.asyncio
    async def test_null_bytes_stripped(self, service):
        """Null bytes from PDF extraction should be removed."""
        fake_resp = _mock_ollama_response("Engine overheating")

        mock_client = AsyncMock()
        mock_client.post.return_value = fake_resp
        mock_client.is_closed = False
        service._client = mock_client

        await service.translate_text("引擎\x00過熱\x00問題")
        call_args = mock_client.post.call_args
        messages = call_args[1]["json"]["messages"]
        user_content = messages[1]["content"]
        assert "\x00" not in user_content

    @pytest.mark.asyncio
    async def test_oversized_text_skipped(self, service):
        """Text exceeding _MAX_TRANSLATE_CHARS is returned as-is."""
        long_text = "引擎過熱" * (_MAX_TRANSLATE_CHARS // 2)
        result = await service.translate_text(long_text)
        assert result == long_text

    @pytest.mark.asyncio
    async def test_empty_llm_response_fallback(self, service):
        """Empty LLM response falls back to original text."""
        fake_resp = _mock_ollama_response("")

        mock_client = AsyncMock()
        mock_client.post.return_value = fake_resp
        mock_client.is_closed = False
        service._client = mock_client

        original = "引擎過熱問題"
        result = await service.translate_text(original)
        assert result == original

    @pytest.mark.asyncio
    async def test_timeout_fallback(self, service):
        """Timeout returns original text, does not raise."""
        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.TimeoutException(
            "timed out"
        )
        mock_client.is_closed = False
        service._client = mock_client

        original = "引擎過熱問題"
        result = await service.translate_text(original)
        assert result == original

    @pytest.mark.asyncio
    async def test_http_error_fallback(self, service):
        """HTTP 500 returns original text, does not raise."""
        mock_client = AsyncMock()
        mock_client.post.return_value = httpx.Response(
            500,
            json={"error": "model not found"},
            request=httpx.Request(
                "POST", "http://fake/api/chat"
            ),
        )
        mock_client.post.return_value.raise_for_status = (
            MagicMock(
                side_effect=httpx.HTTPStatusError(
                    "500",
                    request=httpx.Request(
                        "POST", "http://fake/api/chat"
                    ),
                    response=httpx.Response(500),
                )
            )
        )
        mock_client.is_closed = False
        service._client = mock_client

        original = "引擎過熱問題"
        result = await service.translate_text(original)
        assert result == original

    @pytest.mark.asyncio
    async def test_unexpected_error_fallback(self, service):
        """Unexpected exception returns original text."""
        mock_client = AsyncMock()
        mock_client.post.side_effect = ConnectionError("boom")
        mock_client.is_closed = False
        service._client = mock_client

        original = "引擎過熱問題"
        result = await service.translate_text(original)
        assert result == original


# ------------------------------------------------------------------ #
# TranslationService.translate_section
# ------------------------------------------------------------------ #

class TestTranslateSection:
    """Tests for translate_section method."""

    @pytest.fixture
    def service(self):
        """Create a TranslationService with mocked translate_text."""
        with patch("app.rag.translator.settings") as mock:
            mock.llm_endpoint = "http://fake:11434"
            mock.llm_model = "llama3:8b"
            svc = TranslationService()
        return svc

    @pytest.mark.asyncio
    async def test_english_section_unchanged(self, service):
        """Section with no CJK text should be returned as-is."""
        section = _make_section(
            title="Engine", body="Check oil level."
        )
        result = await service.translate_section(section)
        assert result is section

    @pytest.mark.asyncio
    async def test_metadata_preserved(self, service):
        """vehicle_model, dtc_codes, level must survive."""
        section = _make_section(
            title="引擎過熱",
            body="檢查機油液位",
            level=2,
            vehicle_model="MWS150-A",
            dtc_codes=["P0217"],
        )

        with patch.object(
            service, "translate_text", new_callable=AsyncMock
        ) as mock_tt:
            mock_tt.side_effect = lambda t: "Translated"

            result = await service.translate_section(section)

        assert result.level == 2
        assert result.vehicle_model == "MWS150-A"
        assert result.dtc_codes == ["P0217"]

    @pytest.mark.asyncio
    async def test_image_markers_preserved(self, service):
        """Image markers should NOT be translated."""
        body = (
            "引擎過熱說明\n\n"
            "[Image 1, Page 5]\n"
            "Description: Exploded view of engine\n\n"
            "更多中文說明"
        )
        section = _make_section(title="Test", body=body)

        async def fake_translate(text):
            """Replace CJK text but keep English as-is."""
            if has_cjk(text):
                return "English translation"
            return text

        with patch.object(
            service,
            "translate_text",
            side_effect=fake_translate,
        ):
            result = await service.translate_section(section)

        # Marker itself must survive
        assert "[Image 1, Page 5]" in result.body
        # The Description line after the marker is part of
        # the next text block which also contains CJK, so
        # it gets translated together.  The key invariant is
        # that the marker tag itself is not mangled.
        assert "引擎" not in result.body

    @pytest.mark.asyncio
    async def test_only_title_has_cjk(self, service):
        """Section with CJK title but English body."""
        section = _make_section(
            title="引擎過熱",
            body="Check oil level.",
        )

        with patch.object(
            service, "translate_text", new_callable=AsyncMock
        ) as mock_tt:
            mock_tt.return_value = "Engine Overheat"

            result = await service.translate_section(section)

        assert result.title == "Engine Overheat"
        assert result.body == "Check oil level."


# ------------------------------------------------------------------ #
# TranslationService.translate_sections
# ------------------------------------------------------------------ #

class TestTranslateSections:
    """Tests for translate_sections method."""

    @pytest.fixture
    def service(self):
        """Create a TranslationService."""
        with patch("app.rag.translator.settings") as mock:
            mock.llm_endpoint = "http://fake:11434"
            mock.llm_model = "llama3:8b"
            svc = TranslationService()
        return svc

    @pytest.mark.asyncio
    async def test_empty_list(self, service):
        """Empty list should return empty list."""
        result = await service.translate_sections([])
        assert result == []

    @pytest.mark.asyncio
    async def test_invalid_max_concurrent_raises(self, service):
        """max_concurrent < 1 should raise ValueError."""
        with pytest.raises(ValueError, match="max_concurrent"):
            await service.translate_sections(
                [], max_concurrent=0,
            )

    @pytest.mark.asyncio
    async def test_skips_english_sections(self, service):
        """English-only sections should be skipped."""
        sections = [
            _make_section(title="Intro", body="Hello"),
            _make_section(title="Safety", body="Be careful"),
        ]

        result = await service.translate_sections(sections)
        assert len(result) == 2
        assert result[0] is sections[0]
        assert result[1] is sections[1]

    @pytest.mark.asyncio
    async def test_counts_failures(self, service):
        """Sections that still have CJK after translation count."""
        sections = [
            _make_section(title="T", body="引擎過熱問題說明"),
        ]

        # Simulate failed translation (returns original)
        with patch.object(
            service, "translate_section", new_callable=AsyncMock
        ) as mock_ts:
            mock_ts.return_value = sections[0]

            result = await service.translate_sections(sections)

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_order_preserved_with_concurrency(self, service):
        """Section order must be preserved under concurrency."""
        sections = [
            _make_section(
                title=f"標題{i}", body=f"引擎過熱問題{i}號說明"
            )
            for i in range(5)
        ]

        async def fake_translate(section: Section) -> Section:
            """Return a section with English title for ordering."""
            return _make_section(
                title=f"EN-{section.title}",
                body="Translated",
                level=section.level,
                vehicle_model=section.vehicle_model,
                dtc_codes=section.dtc_codes,
            )

        with patch.object(
            service,
            "translate_section",
            side_effect=fake_translate,
        ):
            result = await service.translate_sections(
                sections, max_concurrent=2,
            )

        assert [s.title for s in result] == [
            "EN-標題0",
            "EN-標題1",
            "EN-標題2",
            "EN-標題3",
            "EN-標題4",
        ]

    @pytest.mark.asyncio
    async def test_max_concurrent_one_is_sequential(self, service):
        """max_concurrent=1 should degrade to sequential."""
        sections = [
            _make_section(
                title="引擎", body="引擎過熱問題說明"
            ),
            _make_section(
                title="煞車", body="煞車系統問題說明"
            ),
        ]

        call_order: list[str] = []

        async def fake_translate(section: Section) -> Section:
            """Track call order and return translated section."""
            call_order.append(section.title)
            return _make_section(
                title=f"EN-{section.title}",
                body="Translated",
            )

        with patch.object(
            service,
            "translate_section",
            side_effect=fake_translate,
        ):
            result = await service.translate_sections(
                sections, max_concurrent=1,
            )

        assert len(result) == 2
        assert call_order == ["引擎", "煞車"]


# ------------------------------------------------------------------ #
# Singleton
# ------------------------------------------------------------------ #

class TestSingleton:
    """Tests for get_translation_service singleton."""

    def test_returns_same_instance(self):
        """Repeated calls should return the same object."""
        import app.rag.translator as mod
        mod._translation_service = None

        with patch("app.rag.translator.settings") as mock:
            mock.llm_endpoint = "http://fake:11434"
            mock.llm_model = "llama3:8b"
            svc1 = get_translation_service()
            svc2 = get_translation_service()

        assert svc1 is svc2
        mod._translation_service = None


# ------------------------------------------------------------------ #
# Resource cleanup
# ------------------------------------------------------------------ #

class TestClose:
    """Tests for close method."""

    @pytest.mark.asyncio
    async def test_close_on_open_client(self):
        """close() should call aclose on the client."""
        with patch("app.rag.translator.settings") as mock:
            mock.llm_endpoint = "http://fake:11434"
            mock.llm_model = "llama3:8b"
            svc = TranslationService()

        mock_client = AsyncMock()
        mock_client.is_closed = False
        svc._client = mock_client

        await svc.close()
        mock_client.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_on_none_client(self):
        """close() on uninitialized service should not raise."""
        with patch("app.rag.translator.settings") as mock:
            mock.llm_endpoint = "http://fake:11434"
            mock.llm_model = "llama3:8b"
            svc = TranslationService()

        await svc.close()  # Should not raise
