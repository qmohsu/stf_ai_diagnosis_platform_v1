"""Unit tests for the golden-candidate generator script.

Uses a temp manual file and a scripted ``AsyncOpenAI`` stand-in so
tests run without OpenRouter access.  Covers: grounding
validation, category-based section filtering, adversarial branch,
malformed-LLM-output handling, duplicate suppression, and ID
assignment.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from unittest.mock import AsyncMock, MagicMock

import pytest

from scripts.generate_golden_candidates import (
    _filter_sections_for_category,
    _make_candidate_id,
    _parse_llm_json,
    _render_toc_sample,
    _strip_fence,
    _truncate_section,
    _validate_and_ground,
    generate_candidates,
)
from app.harness_tools.manual_fs import parse_heading_tree


# ── Sample manual ─────────────────────────────────────────────────


_SAMPLE_MANUAL = """\
---
source_pdf: MWS150A_Service_Manual.pdf
vehicle_model: MWS-150-A
language: en
page_count: 415
section_count: 6
---

# MWS-150-A Service Manual

## Chapter 1: General Information

### 1.1 Specifications

Spark plug torque: 12.5 N-m.
Displacement: 155 cc.
Compression ratio: 10.5:1.

Replace spark plug every 6000 km.

### 1.2 Tools Required

Standard metric tool set.

## Chapter 3: Fuel System

### 3.1 Overview

The fuel system consists of a tank, pump, and injector.

### 3.2 Fuel System Troubleshooting

DTC P0171 indicates a system too lean condition on bank 1.

**Diagnostic Steps:**
1. Inspect intake manifold for vacuum leaks.
2. Measure fuel pressure at the rail.
3. Inspect the fuel injector for clogging.

![Fuel injector](images/MWS150A_Service_Manual/p045-1.png)

*Vision description: Exploded view of the fuel injector.*

## Appendix: DTC Index

| DTC | Description | Section |
|-----|-------------|---------|
| P0171 | System Too Lean | 3.2 Fuel System Troubleshooting |
"""


@pytest.fixture()
def manual_dir(tmp_path: Path) -> Path:
    """Write the sample manual into a temp directory."""
    md_path = tmp_path / "MWS150A_Service_Manual.md"
    md_path.write_text(_SAMPLE_MANUAL, encoding="utf-8")
    return tmp_path


# ── Scripted OpenAI client ────────────────────────────────────────


class _ScriptedClient:
    """Minimal stand-in for ``AsyncOpenAI`` with scripted replies.

    Each queued reply is either a ``str``, a ``Reply`` builder
    callable, or an ``Exception`` to raise.  Callables receive
    the raw ``**kwargs`` that would have gone to OpenAI — useful
    for deriving the reply from the user prompt (e.g., picking
    the same slug the generator sampled).  Records every call
    for assertions.
    """

    def __init__(
        self,
        replies: List[Any],
    ) -> None:
        self._replies = list(replies)
        self.calls: List[Dict[str, Any]] = []
        self.chat = MagicMock()
        self.chat.completions = MagicMock()
        self.chat.completions.create = AsyncMock(
            side_effect=self._respond,
        )

    async def _respond(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self._replies:
            raise RuntimeError("ScriptedClient exhausted")
        nxt = self._replies.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        if callable(nxt):
            content = nxt(kwargs)
        else:
            content = nxt
        msg = MagicMock()
        msg.content = content
        choice = MagicMock()
        choice.message = msg
        completion = MagicMock()
        completion.choices = [choice]
        return completion


def _extract_prompt_field(
    kwargs: Dict[str, Any], field_name: str,
) -> str:
    """Extract ``field_name: <value>`` from the user prompt."""
    user_content = ""
    for msg in kwargs.get("messages", []):
        if msg.get("role") == "user":
            user_content = msg.get("content", "")
            break
    for line in user_content.splitlines():
        if line.startswith(f"{field_name}:"):
            return line.split(":", 1)[1].strip()
    return ""


def _extract_section_text_from_prompt(
    kwargs: Dict[str, Any],
) -> str:
    """Extract the '## Section text\\n...\\n## Instruction' block."""
    user_content = ""
    for msg in kwargs.get("messages", []):
        if msg.get("role") == "user":
            user_content = msg.get("content", "")
            break
    marker_start = "## Section text\n"
    marker_end = "\n## Instruction"
    start = user_content.find(marker_start)
    end = user_content.find(marker_end)
    if start < 0 or end < 0:
        return ""
    return user_content[start + len(marker_start):end]


def _slug_aware_reply(
    must_contain: List[str], quote_picker: str = "first_line",
) -> Any:
    """Return a callable that builds a reply matching the prompt.

    The callable inspects ``kwargs`` for ``manual_id`` / ``slug``
    and grabs a quote from the section text (first non-empty line
    with useful content) so grounding validation passes regardless
    of which section the generator sampled.
    """

    def _builder(kwargs: Dict[str, Any]) -> str:
        manual_id = _extract_prompt_field(kwargs, "manual_id")
        slug = _extract_prompt_field(kwargs, "slug")
        section = _extract_section_text_from_prompt(kwargs)
        # Pick a reasonable quote — first line >= 10 chars that
        # isn't a heading marker.
        quote = "placeholder"
        for line in section.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith(("#", "!", "*")):
                continue
            if len(stripped) >= 10:
                quote = stripped[:120]
                break
        return json.dumps({
            "question": "What is the spec value?",
            "golden_summary": (
                "The cited section provides the answer."
            ),
            "golden_citations": [{
                "manual_id": manual_id,
                "slug": slug,
                "quote": quote,
            }],
            "must_contain": must_contain,
            "must_not_contain": [],
            "expected_tool_trace": [
                "get_manual_toc", "read_manual_section",
            ],
            "requires_image": False,
        })

    return _builder


# ── Pure helpers ──────────────────────────────────────────────────


class TestStripFence:
    def test_strips_json_fence(self) -> None:
        assert _strip_fence('```json\n{"a": 1}\n```') == (
            '{"a": 1}'
        )

    def test_passes_through_plain(self) -> None:
        assert _strip_fence("  plain ") == "plain"


class TestParseLlmJson:
    def test_valid_json(self) -> None:
        assert _parse_llm_json('{"x": 1}') == {"x": 1}

    def test_markdown_fenced(self) -> None:
        assert _parse_llm_json(
            '```json\n{"x": 1}\n```',
        ) == {"x": 1}

    def test_extracts_from_prose(self) -> None:
        raw = 'Sure!\n{"x": 2}\nLet me know.'
        assert _parse_llm_json(raw) == {"x": 2}

    def test_none_input(self) -> None:
        assert _parse_llm_json(None) is None

    def test_malformed(self) -> None:
        assert _parse_llm_json("no json here") is None


class TestMakeCandidateId:
    def test_strips_service_manual_suffix(self) -> None:
        assert _make_candidate_id(
            "MWS150A_Service_Manual", "dtc", 1,
        ) == "mws150a-dtc-001"

    def test_zero_pads_sequence(self) -> None:
        assert _make_candidate_id(
            "MWS150A_Service_Manual", "component", 42,
        ) == "mws150a-component-042"


class TestTruncateSection:
    def test_short_text_unchanged(self) -> None:
        assert _truncate_section("short", 100) == "short"

    def test_long_text_truncated(self) -> None:
        text = "a\n" * 5000  # 10K chars
        truncated = _truncate_section(text, 1000)
        assert "[... section truncated" in truncated
        assert len(truncated) < 1500


class TestRenderTocSample:
    def test_produces_indented_outline(self) -> None:
        tree = parse_heading_tree(_SAMPLE_MANUAL)
        toc = _render_toc_sample(tree, max_lines=20)
        assert "MWS-150-A Service Manual" in toc
        assert "Chapter 1: General Information" in toc
        assert "3.2 Fuel System Troubleshooting" in toc


# ── Section filter ────────────────────────────────────────────────


class TestFilterSectionsForCategory:
    def test_dtc_prefers_troubleshooting(self) -> None:
        tree = parse_heading_tree(_SAMPLE_MANUAL)
        eligible = _filter_sections_for_category(
            _SAMPLE_MANUAL, tree, "dtc",
        )
        titles = [n.title for n in eligible]
        assert any(
            "Troubleshooting" in t for t in titles
        )

    def test_image_filters_sections_with_images(self) -> None:
        tree = parse_heading_tree(_SAMPLE_MANUAL)
        eligible = _filter_sections_for_category(
            _SAMPLE_MANUAL, tree, "image",
        )
        # Only the fuel-system-troubleshooting section has an
        # image ref in the sample manual.
        assert len(eligible) >= 1
        assert all(
            "Troubleshooting" in n.title
            or n.level == 1
            or "Service Manual" in n.title
            for n in eligible
        )

    def test_adversarial_returns_empty(self) -> None:
        tree = parse_heading_tree(_SAMPLE_MANUAL)
        eligible = _filter_sections_for_category(
            _SAMPLE_MANUAL, tree, "adversarial",
        )
        assert eligible == []

    def test_fallback_when_no_match(self) -> None:
        # Use text that has no DTC/symptom patterns at all.
        tree = parse_heading_tree("# Title\n\nBare text.\n")
        eligible = _filter_sections_for_category(
            "# Title\n\nBare text.\n", tree, "dtc",
        )
        # Falls back to all nodes long enough — but the single
        # short body here is below _MIN_SECTION_CHARS, so empty.
        assert eligible == []


# ── Grounding validation ──────────────────────────────────────────


class TestValidateAndGround:
    def _minimal_payload(
        self, manual_id: str, slug: str, quote: str,
    ) -> Dict[str, Any]:
        return {
            "question": "What is the spark plug torque?",
            "golden_summary": "12.5 N-m.",
            "golden_citations": [{
                "manual_id": manual_id,
                "slug": slug,
                "quote": quote,
            }],
            "must_contain": ["spark plug", "12.5"],
            "must_not_contain": [],
            "expected_tool_trace": [
                "get_manual_toc",
                "read_manual_section",
            ],
            "requires_image": False,
        }

    def test_grounded_happy_path(self) -> None:
        section = (
            "Spark plug torque: 12.5 N-m.  Replace every 6000 km."
        )
        payload = self._minimal_payload(
            "MWS150A_Service_Manual",
            "1-1-specifications",
            "Spark plug torque: 12.5 N-m.",
        )
        cand, err = _validate_and_ground(
            payload,
            "MWS150A_Service_Manual",
            "1-1-specifications",
            section,
            "component",
        )
        assert err is None
        assert cand is not None
        assert cand["category"] == "component"
        assert cand["difficulty"] == "medium"
        assert cand["id"] == ""  # assigned by caller

    def test_missing_fields_rejected(self) -> None:
        payload = {"question": "x"}
        cand, err = _validate_and_ground(
            payload, "M", "s", "text", "dtc",
        )
        assert cand is None
        assert err and "missing fields" in err

    def test_quote_not_in_section_rejected(self) -> None:
        payload = self._minimal_payload(
            "MWS150A_Service_Manual",
            "1-1-specifications",
            "This quote is not in the section",
        )
        cand, err = _validate_and_ground(
            payload,
            "MWS150A_Service_Manual",
            "1-1-specifications",
            "Spark plug torque: 12.5 N-m.",
            "component",
        )
        assert cand is None
        assert err and "quote not found" in err

    def test_quote_with_whitespace_differences_accepted(
        self,
    ) -> None:
        """Quote matches modulo whitespace — e.g. LLM joined
        a line-wrapped source passage with '\\n' while the
        original has the same text split across lines.  This
        is the dominant failure mode on Chinese manuals where
        the PDF converter leaves mid-sentence line breaks."""
        section = (
            "Spark plug torque:\n"
            "12.5    N-m.\n\n"
            "Replace every 6000 km."
        )
        payload = self._minimal_payload(
            "MWS150A_Service_Manual",
            "1-1-specifications",
            # Single-line quote with one space between
            # tokens — does NOT appear verbatim in the
            # section, but should match after whitespace
            # normalisation.
            "Spark plug torque: 12.5 N-m.",
        )
        cand, err = _validate_and_ground(
            payload,
            "MWS150A_Service_Manual",
            "1-1-specifications",
            section,
            "component",
        )
        assert err is None, f"unexpected error: {err}"
        assert cand is not None

    def test_cjk_quote_with_mid_word_line_break_accepted(
        self,
    ) -> None:
        """CJK gap dropping: a Chinese word split across a line
        break in the source must still match the LLM's
        single-line reconstruction.  Without the CJK-aware
        normalisation, ``"節流閥位置感\\n知器"`` would collapse
        to ``"節流閥位置感 知器"`` which is invalid Chinese
        (no word boundaries)."""
        section = "P0122 節流閥位置感\n知器 搭鐵短路"
        payload = self._minimal_payload(
            "MWS150A_Service_Manual",
            "3-2-fuel-system",
            # LLM reconstruction — no embedded newline.
            "節流閥位置感知器",
        )
        cand, err = _validate_and_ground(
            payload,
            "MWS150A_Service_Manual",
            "3-2-fuel-system",
            section,
            "component",
        )
        assert err is None, f"unexpected error: {err}"
        assert cand is not None

    def test_cjk_quote_preserves_latin_space_boundaries(
        self,
    ) -> None:
        """Space between Latin and CJK is preserved — ``"DTC
        P0122"`` should NOT collapse into a single token."""
        section = "See DTC P0122 節流閥."
        # Quote contains the Latin-CJK boundary intact.
        payload = self._minimal_payload(
            "MWS150A_Service_Manual",
            "sec",
            "DTC P0122 節流閥",
        )
        cand, err = _validate_and_ground(
            payload,
            "MWS150A_Service_Manual",
            "sec",
            section,
            "component",
        )
        assert err is None, f"unexpected error: {err}"
        assert cand is not None

    def test_manual_id_mismatch_rejected(self) -> None:
        payload = self._minimal_payload(
            "WRONG_MANUAL",
            "1-1-specifications",
            "Spark plug torque: 12.5 N-m.",
        )
        cand, err = _validate_and_ground(
            payload,
            "MWS150A_Service_Manual",
            "1-1-specifications",
            "Spark plug torque: 12.5 N-m.",
            "component",
        )
        assert cand is None
        assert err and "manual_id mismatch" in err

    def test_slug_mismatch_rejected(self) -> None:
        payload = self._minimal_payload(
            "MWS150A_Service_Manual",
            "wrong-slug",
            "Spark plug torque: 12.5 N-m.",
        )
        cand, err = _validate_and_ground(
            payload,
            "MWS150A_Service_Manual",
            "1-1-specifications",
            "Spark plug torque: 12.5 N-m.",
            "component",
        )
        assert cand is None
        assert err and "slug mismatch" in err

    def test_grounded_entry_without_citations_rejected(
        self,
    ) -> None:
        payload = {
            "question": "q",
            "golden_summary": "s",
            "golden_citations": [],
            "must_contain": ["x"],
            "must_not_contain": [],
            "expected_tool_trace": ["read_manual_section"],
            "requires_image": False,
        }
        cand, err = _validate_and_ground(
            payload, "M", "s", "text", "component",
        )
        assert cand is None
        assert err and "no citations" in err

    def test_adversarial_happy_path(self) -> None:
        payload = {
            "question": "What does P9999 mean?",
            "golden_summary": (
                "Not found: P9999 is not documented."
            ),
            "golden_citations": [],
            "must_contain": ["not found"],
            "must_not_contain": [
                "P9999 is caused by",
            ],
            "expected_tool_trace": ["get_manual_toc"],
            "requires_image": False,
        }
        cand, err = _validate_and_ground(
            payload,
            "MWS150A_Service_Manual",
            None,
            None,
            "adversarial",
        )
        assert err is None
        assert cand is not None
        assert cand["difficulty"] == "hard"
        assert cand["golden_citations"] == []

    def test_adversarial_with_citations_rejected(self) -> None:
        payload = {
            "question": "q",
            "golden_summary": "Not found: x",
            "golden_citations": [{
                "manual_id": "M",
                "slug": "s",
                "quote": "q",
            }],
            "must_contain": ["not found"],
            "must_not_contain": [],
            "expected_tool_trace": ["get_manual_toc"],
        }
        cand, err = _validate_and_ground(
            payload,
            "MWS150A_Service_Manual",
            None, None, "adversarial",
        )
        assert cand is None
        assert err and "empty golden_citations" in err

    def test_adversarial_without_not_found_rejected(
        self,
    ) -> None:
        payload = {
            "question": "q",
            "golden_summary": "x",
            "golden_citations": [],
            "must_contain": ["something else"],
            "must_not_contain": [],
            "expected_tool_trace": ["get_manual_toc"],
        }
        cand, err = _validate_and_ground(
            payload,
            "MWS150A_Service_Manual",
            None, None, "adversarial",
        )
        assert cand is None
        assert err and "'not found'" in err


# ── End-to-end generator ──────────────────────────────────────────


def _candidate_json(
    manual_id: str, slug: str, quote: str,
) -> str:
    """Build a well-formed generator response JSON string."""
    return json.dumps({
        "question": "What is the spark plug torque?",
        "golden_summary": (
            "Spark plug torque is 12.5 N-m per the "
            "specifications section."
        ),
        "golden_citations": [{
            "manual_id": manual_id,
            "slug": slug,
            "quote": quote,
        }],
        "must_contain": ["spark plug", "12.5"],
        "must_not_contain": [],
        "expected_tool_trace": [
            "get_manual_toc", "read_manual_section",
        ],
        "requires_image": False,
    })


class TestGenerateCandidatesEndToEnd:

    @pytest.mark.asyncio
    async def test_happy_path_component_category(
        self, manual_dir: Path,
    ) -> None:
        """Generator returns one grounded candidate for component."""
        client = _ScriptedClient([
            _slug_aware_reply(
                must_contain=["spark plug", "torque"],
            ),
        ])
        results = await generate_candidates(
            manual_id="MWS150A_Service_Manual",
            category="component",
            count=1,
            model="test-model",
            manual_dir=manual_dir,
            client=client,
            rng=random.Random(42),
        )
        assert len(results) == 1
        assert results[0]["id"] == "mws150a-component-001"
        assert (
            results[0]["golden_citations"][0]["manual_id"]
            == "MWS150A_Service_Manual"
        )
        # Slug varies with rng choice — just verify it's one of
        # the known sections.
        assert results[0]["golden_citations"][0]["slug"]

    @pytest.mark.asyncio
    async def test_grounding_rejects_fabricated_quote(
        self, manual_dir: Path,
    ) -> None:
        """LLM returns a quote not in section -> candidate dropped."""
        bad = _candidate_json(
            "MWS150A_Service_Manual",
            "1-1-specifications",
            "This quote does not appear anywhere",
        )
        client = _ScriptedClient([bad])
        results = await generate_candidates(
            manual_id="MWS150A_Service_Manual",
            category="component",
            count=1,
            model="test-model",
            manual_dir=manual_dir,
            client=client,
            rng=random.Random(42),
        )
        assert results == []

    @pytest.mark.asyncio
    async def test_duplicates_are_deduped(
        self, manual_dir: Path,
    ) -> None:
        """Same question twice -> only one candidate kept."""
        reply_builder = _slug_aware_reply(
            must_contain=["spark plug"],
        )
        client = _ScriptedClient([
            reply_builder, reply_builder, reply_builder,
        ])
        results = await generate_candidates(
            manual_id="MWS150A_Service_Manual",
            category="component",
            count=3,
            model="test-model",
            manual_dir=manual_dir,
            client=client,
            rng=random.Random(42),
        )
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_malformed_llm_response_skipped(
        self, manual_dir: Path,
    ) -> None:
        """Non-JSON output is skipped, next valid reply kept."""
        client = _ScriptedClient([
            "not json",
            _slug_aware_reply(must_contain=["spec"]),
        ])
        results = await generate_candidates(
            manual_id="MWS150A_Service_Manual",
            category="component",
            count=2,
            model="test-model",
            manual_dir=manual_dir,
            client=client,
            rng=random.Random(42),
        )
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_unknown_manual_raises(
        self, manual_dir: Path,
    ) -> None:
        """Missing manual .md file raises FileNotFoundError."""
        client = _ScriptedClient([])
        with pytest.raises(FileNotFoundError):
            await generate_candidates(
                manual_id="DoesNotExist",
                category="component",
                count=1,
                model="m",
                manual_dir=manual_dir,
                client=client,
            )

    @pytest.mark.asyncio
    async def test_unknown_category_raises(
        self, manual_dir: Path,
    ) -> None:
        """Invalid category raises ValueError before any LLM call."""
        client = _ScriptedClient([])
        with pytest.raises(ValueError):
            await generate_candidates(
                manual_id="MWS150A_Service_Manual",
                category="not_a_real_category",
                count=1,
                model="m",
                manual_dir=manual_dir,
                client=client,
            )

    @pytest.mark.asyncio
    async def test_adversarial_branch(
        self, manual_dir: Path,
    ) -> None:
        """Adversarial category takes the metadata+TOC path."""
        reply = json.dumps({
            "question": "What is DTC P9999 on MWS-150-A?",
            "golden_summary": (
                "Not found: P9999 is not in the manual."
            ),
            "golden_citations": [],
            "must_contain": ["not found"],
            "must_not_contain": [
                "P9999 is caused by",
            ],
            "expected_tool_trace": ["get_manual_toc"],
            "requires_image": False,
        })
        client = _ScriptedClient([reply])
        results = await generate_candidates(
            manual_id="MWS150A_Service_Manual",
            category="adversarial",
            count=1,
            model="m",
            manual_dir=manual_dir,
            client=client,
        )
        assert len(results) == 1
        assert results[0]["id"] == "mws150a-adversarial-001"
        assert results[0]["golden_citations"] == []
