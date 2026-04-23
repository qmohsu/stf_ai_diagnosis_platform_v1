#!/usr/bin/env python3
"""Generate grounded golden candidates for the manual-agent eval.

HARNESS-14 phase 3.  Reads a real ingested service manual from
``settings.manual_storage_path``, samples sections, and asks an LLM
to produce ``(question, summary, citations, must_contain,
must_not_contain, expected_tool_trace)`` tuples grounded in the
sampled text.  Every citation's ``quote`` is verified to be a
substring of the source section; candidates that fail grounding
are dropped before being written.

Output lands in ``tests/harness/evals/golden/candidates/`` as
JSONL, ready for human review via ``review_golden_candidates.py``.
**Never** write directly to ``golden/v1/`` — promotion is a
deliberate reviewer action.

Categories:
  - ``dtc``: DTC lookup questions sampled from troubleshooting
    sections.
  - ``symptom``: Symptom-based questions (misfire, overheat, etc.).
  - ``component``: Specification / torque / replacement procedure
    questions sampled from reference or maintenance sections.
  - ``image``: Questions whose expected answer requires a figure
    (wiring diagram, exploded view).  Only sections containing
    image references are sampled.
  - ``adversarial``: Intra-manual edge cases (fake DTC, wrong
    vehicle type, typo'd slug, multi-section answer).  The LLM
    is instructed to craft a question the manual CANNOT answer
    and set the golden answer to a "Not found: ..." response
    with empty citations.

Usage::

    python -m scripts.generate_golden_candidates \\
        --manual MWS150A_Service_Manual \\
        --category dtc \\
        --count 10 \\
        --out tests/harness/evals/golden/candidates/\\
mws150a-dtc.jsonl

    # Override model (default: deepseek/deepseek-v3.2, chosen to
    # differ from the judge's z-ai/glm-5.1).
    python -m scripts.generate_golden_candidates \\
        --manual MWS150A_Service_Manual \\
        --category component \\
        --count 5 \\
        --model moonshotai/kimi-k2 \\
        --out ...

Requirements:
  - ``PREMIUM_LLM_API_KEY`` set in the environment.
  - The target manual already ingested to
    ``settings.manual_storage_path``.

Author: Li-Ta Hsu
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openai import AsyncOpenAI

from app.config import settings
from app.harness_tools.manual_fs import (
    HeadingNode,
    extract_section,
    parse_frontmatter,
    parse_heading_tree,
)

# ── Logging (plain, not structlog — this is a CLI script) ─────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("generate_golden_candidates")


# ── Constants ─────────────────────────────────────────────────────

_DEFAULT_MODEL = "deepseek/deepseek-v3.2"
"""Default generator model.  Chosen to differ from the judge's
``z-ai/glm-5.1`` to reduce circularity risk — a generator and
judge with different biases produce a more robust eval.
Override with ``--model``."""

_TEMPERATURE = 0.4
"""Generator temperature.  Some variety is desirable so a batch
of candidates isn't 10 near-identical questions."""

_MAX_TOKENS = 1536
"""Output cap per generation call.  A full candidate is ~800
tokens; extra headroom absorbs occasional verbosity."""

_MIN_SECTION_CHARS = 200
"""Sections shorter than this are skipped — too thin for a
grounded question."""

_MAX_SECTION_CHARS = 8000
"""Cap on section text sent to the generator.  Longer sections
are truncated at a line boundary near this limit."""

_CATEGORIES = {
    "dtc", "symptom", "component", "image", "adversarial",
}

_DTC_HINT_RE = re.compile(
    r"(dtc|diagnos|troubleshoot|p0\d{3}|p1\d{3}|fault code)",
    re.IGNORECASE,
)
_COMPONENT_HINT_RE = re.compile(
    r"(specification|torque|replace|inspect|overhaul|"
    r"maintenance|adjust)",
    re.IGNORECASE,
)
_SYMPTOM_HINT_RE = re.compile(
    r"(troubleshoot|symptom|misfire|overheat|leak|noise|"
    r"vibration|stall)",
    re.IGNORECASE,
)
_IMAGE_REF_RE = re.compile(r"!\[[^\]]*\]\(images/")


# ── Prompt builders ───────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are generating a grounded evaluation entry for a vehicle-
service-manual search agent.

You will be given one section of a real service manual.  Your job
is to produce a single JSON object describing one test case that
the agent should be able to answer by navigating to this section.

Return ONLY the JSON object.  No prose.  No markdown fences.

## Required JSON shape

{
  "question": "A realistic diagnostic inquiry the agent should
                answer, phrased as a technician would ask it.
                3-20 words.",
  "golden_summary": "3-5 sentence reference answer grounded in
                     the provided section text.  Be specific —
                     include values, procedures, or part names.",
  "golden_citations": [
    {
      "manual_id": "<the manual_id you were given>",
      "slug": "<the slug you were given>",
      "quote": "A verbatim span (10-150 chars) from the section
                text.  Must appear EXACTLY in the section."
    }
  ],
  "must_contain": [
    "2-4 short strings that MUST appear (case-insensitive)
     in a correct answer.  Prefer concrete identifiers:
     DTC codes, spec values with units, part names."
  ],
  "must_not_contain": [
    "0-2 strings that MUST NOT appear.  Use to guard against
     common hallucinations — e.g., an incorrect DTC, a wrong
     unit, or a fabricated procedure step.  Keep short."
  ],
  "expected_tool_trace": [
    "A loose guide — typically 2-3 tool names from:
     get_manual_toc, read_manual_section, search_manual,
     list_manuals."
  ],
  "requires_image": false
}

## Quality rules

1. The question must be answerable from the provided section
   text alone — do not require knowledge from other sections.
2. Every quote in golden_citations must be a verbatim
   substring of the provided section text (no paraphrasing,
   no ellipses except in the original).
3. must_contain strings should appear verbatim somewhere in
   the section text or be obviously derivable from it.
4. For "requires_image": set to true only if the correct
   answer references a figure/diagram that appears in the
   section (```![...](images/...)`` markdown present).
"""


_ADVERSARIAL_SYSTEM_PROMPT = """\
You are generating a grounded ADVERSARIAL evaluation entry for
a vehicle-service-manual search agent.  The goal is to test
that the agent correctly REFUSES to answer when the information
is not available, instead of hallucinating.

You will be given the manual's overall metadata (vehicle model,
page count) and a short sample of its table of contents.  Craft
a question that the manual CANNOT answer — choose one flavour:

  - "fake_dtc": A plausible-looking DTC that is NOT actually
    documented anywhere in this manual (pick a P-code not
    present in the TOC samples).
  - "out_of_scope": A question about a different vehicle class
    (e.g., truck parts for a scooter manual).
  - "nonexistent_component": A part or system that this manual
    does not cover.

Return ONLY the JSON object.  No prose.  No fences.

## Required JSON shape

{
  "question": "The adversarial question.",
  "golden_summary": "A reference answer in the form:
                     'Not found: <one-sentence reason>.'
                     E.g., 'Not found: P9999 is not documented
                     in the MWS-150-A service manual.'",
  "golden_citations": [],
  "must_contain": ["not found"],
  "must_not_contain": [
    "A plausible-but-fabricated claim the agent MUST NOT make.
     E.g., if the question is about DTC P9999, include
     'P9999 is caused by' or 'replace the P9999 sensor' —
     anything that looks like made-up content."
  ],
  "expected_tool_trace": [
    "get_manual_toc"
  ],
  "requires_image": false
}
"""


def _user_prompt_grounded(
    manual_id: str,
    slug: str,
    section_text: str,
    category: str,
) -> str:
    """Build the user prompt for a grounded (non-adversarial) entry.

    Args:
        manual_id: Filename stem the agent should cite.
        slug: Section slug the agent should cite.
        section_text: Actual section content (truncated).
        category: One of ``dtc``, ``symptom``, ``component``,
            ``image``.

    Returns:
        Fully-rendered user prompt.
    """
    category_hint = {
        "dtc": (
            "The question should be a DTC lookup (what does "
            "this code mean, what is the recommended "
            "diagnostic procedure)."
        ),
        "symptom": (
            "The question should describe a symptom and ask "
            "what to investigate (e.g., 'engine misfires at "
            "idle, what should I check')."
        ),
        "component": (
            "The question should ask about a specification, "
            "torque value, replacement interval, or a "
            "maintenance procedure."
        ),
        "image": (
            "The question should require a figure/diagram to "
            "answer completely (wiring diagram, exploded "
            "view, diagnostic flowchart).  Set "
            "requires_image=true."
        ),
    }[category]

    return f"""\
manual_id: {manual_id}
slug: {slug}
category: {category}

## Category hint
{category_hint}

## Section text
{section_text}

## Instruction
Produce one JSON entry per the system prompt.  Ground every
citation quote in the section text above.
"""


def _user_prompt_adversarial(
    manual_id: str,
    vehicle_model: str,
    page_count: Any,
    toc_sample: str,
) -> str:
    """Build the user prompt for an adversarial entry."""
    return f"""\
manual_id: {manual_id}
vehicle_model: {vehicle_model}
page_count: {page_count}

## Sample of the TOC
{toc_sample}

## Instruction
Produce one JSON entry per the system prompt.  Choose one of
the adversarial flavours and craft a question the manual
CANNOT answer.  Remember: golden_citations must be empty and
must_contain must include "not found".
"""


# ── Section sampling ──────────────────────────────────────────────


def _flatten_headings(
    nodes: List[HeadingNode],
) -> List[HeadingNode]:
    """Flatten a nested heading tree to a list."""
    out: List[HeadingNode] = []
    for node in nodes:
        out.append(node)
        out.extend(_flatten_headings(node.children))
    return out


def _filter_sections_for_category(
    md_text: str,
    tree: List[HeadingNode],
    category: str,
) -> List[HeadingNode]:
    """Pick candidate sections likely to suit a category.

    Heuristic — never perfect, but biases sampling so we don't
    ask the LLM to invent DTC questions from a parts-list
    section.

    Args:
        md_text: Full manual markdown.
        tree: Heading tree.
        category: Category being generated.

    Returns:
        Filtered list of heading nodes.  Falls back to the full
        flat list when no matches are found so generation can
        still proceed (with lower quality).
    """
    all_nodes = _flatten_headings(tree)
    if category == "adversarial":
        return []  # Caller uses metadata + TOC sample instead.

    lines = md_text.split("\n")
    patterns: Dict[str, re.Pattern] = {
        "dtc": _DTC_HINT_RE,
        "symptom": _SYMPTOM_HINT_RE,
        "component": _COMPONENT_HINT_RE,
    }

    filtered: List[HeadingNode] = []
    for node in all_nodes:
        body = "\n".join(
            lines[node.line_start:node.line_end],
        )
        if len(body) < _MIN_SECTION_CHARS:
            continue
        if category == "image":
            if _IMAGE_REF_RE.search(body):
                filtered.append(node)
            continue
        pattern = patterns.get(category)
        if pattern is None:
            filtered.append(node)
            continue
        if pattern.search(node.title) or pattern.search(body):
            filtered.append(node)

    # Fallback: if filtering dropped everything, use all nodes
    # long enough to support a grounded question.  The LLM still
    # might produce a weak candidate — the reviewer catches that.
    if not filtered:
        logger.warning(
            "no sections matched category %s; "
            "falling back to all sections",
            category,
        )
        for node in all_nodes:
            body_len = sum(
                len(line) + 1 for line in
                lines[node.line_start:node.line_end]
            )
            if body_len >= _MIN_SECTION_CHARS:
                filtered.append(node)
    return filtered


def _truncate_section(text: str, max_chars: int) -> str:
    """Truncate text at a line boundary near ``max_chars``."""
    if len(text) <= max_chars:
        return text
    clipped = text[:max_chars]
    last_nl = clipped.rfind("\n")
    if last_nl > max_chars // 2:
        clipped = clipped[:last_nl]
    return clipped + "\n[... section truncated for generation]"


def _render_toc_sample(
    tree: List[HeadingNode], max_lines: int = 30,
) -> str:
    """Render a short TOC sample for adversarial prompts."""
    lines: List[str] = []
    for node in _flatten_headings(tree):
        if len(lines) >= max_lines:
            lines.append("... (more sections omitted)")
            break
        indent = "  " * (node.level - 1)
        lines.append(f"{indent}- {node.title}")
    return "\n".join(lines)


# ── Grounding validation ──────────────────────────────────────────


def _validate_and_ground(
    payload: Dict[str, Any],
    manual_id: str,
    slug: Optional[str],
    section_text: Optional[str],
    category: str,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Check that the generated payload is well-formed and grounded.

    Ensures the LLM returned the expected fields, citations match
    the provided source slug (for non-adversarial), and every
    quote appears as a substring of the section text.

    Args:
        payload: Parsed JSON from the LLM.
        manual_id: Expected manual_id.
        slug: Expected slug (None for adversarial).
        section_text: Source text to ground quotes against (None
            for adversarial).
        category: Category being generated.

    Returns:
        ``(candidate, None)`` on success where ``candidate`` is
        the full ``GoldenEntry``-shaped dict with ``id``,
        ``category``, ``difficulty``, ``notes``, ``obd_context``
        filled in.  ``(None, reason)`` on failure.
    """
    required = {
        "question", "golden_summary", "golden_citations",
        "must_contain", "must_not_contain",
        "expected_tool_trace",
    }
    missing = required - set(payload.keys())
    if missing:
        return None, f"missing fields: {sorted(missing)}"

    if not isinstance(payload["question"], str):
        return None, "question must be string"
    if not isinstance(payload["golden_summary"], str):
        return None, "golden_summary must be string"

    citations = payload.get("golden_citations", [])
    if not isinstance(citations, list):
        return None, "golden_citations must be list"

    if category == "adversarial":
        if citations:
            return None, (
                "adversarial entries must have empty "
                "golden_citations"
            )
        if "not found" not in [
            s.lower() for s in payload.get("must_contain", [])
        ]:
            return None, (
                "adversarial must_contain must include "
                "'not found'"
            )
    else:
        if not citations:
            return None, "grounded entry has no citations"
        for cit in citations:
            if not isinstance(cit, dict):
                return None, "citation must be object"
            cit_manual = cit.get("manual_id")
            cit_slug = cit.get("slug")
            quote = cit.get("quote", "")
            if cit_manual != manual_id:
                return None, (
                    f"citation manual_id mismatch: "
                    f"{cit_manual} != {manual_id}"
                )
            if cit_slug != slug:
                return None, (
                    f"citation slug mismatch: "
                    f"{cit_slug} != {slug}"
                )
            if not isinstance(quote, str) or not quote:
                return None, "citation quote missing"
            if section_text and quote not in section_text:
                return None, (
                    f"quote not found in section: "
                    f"{quote[:60]!r}"
                )

    # Derive difficulty from category as a reasonable default.
    # Reviewer can tweak.
    difficulty = {
        "dtc": "easy",
        "component": "medium",
        "symptom": "medium",
        "image": "medium",
        "adversarial": "hard",
    }.get(category, "medium")

    return (
        {
            "id": "",  # assigned later by caller
            "category": category,
            "difficulty": difficulty,
            "question": payload["question"].strip(),
            "obd_context": payload.get("obd_context"),
            "golden_summary": (
                payload["golden_summary"].strip()
            ),
            "golden_citations": citations,
            "expected_tool_trace": payload[
                "expected_tool_trace"
            ],
            "must_contain": payload["must_contain"],
            "must_not_contain": payload[
                "must_not_contain"
            ],
            "requires_image": bool(
                payload.get("requires_image", False),
            ),
            "notes": "auto-generated; review before promotion",
        },
        None,
    )


# ── LLM call ──────────────────────────────────────────────────────


_MARKDOWN_FENCE_RE = re.compile(
    r"^\s*```(?:json)?\s*\n?(.*?)\n?\s*```\s*$",
    re.DOTALL | re.IGNORECASE,
)


def _strip_fence(content: str) -> str:
    match = _MARKDOWN_FENCE_RE.match(content.strip())
    if match:
        return match.group(1).strip()
    return content.strip()


def _parse_llm_json(content: Optional[str]) -> Optional[Dict[str, Any]]:
    """Best-effort JSON extraction from an LLM response."""
    if not content:
        return None
    stripped = _strip_fence(content)
    try:
        obj = json.loads(stripped)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    # Fallback: first {...} block.
    match = re.search(r"\{.*\}", stripped, re.DOTALL)
    if match:
        try:
            obj = json.loads(match.group(0))
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            return None
    return None


async def _call_generator(
    client: Any,
    model: str,
    system_prompt: str,
    user_prompt: str,
) -> Optional[Dict[str, Any]]:
    """One LLM call returning the parsed JSON payload, or None."""
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=_TEMPERATURE,
            max_tokens=_MAX_TOKENS,
            response_format={"type": "json_object"},
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("generator API error: %s", exc)
        return None

    if not resp.choices:
        return None
    return _parse_llm_json(resp.choices[0].message.content)


# ── Candidate construction ────────────────────────────────────────


def _make_candidate_id(
    manual_id: str,
    category: str,
    seq: int,
) -> str:
    """Build a stable candidate ID.

    Format: ``{manual_short}-{category}-{NNN}`` where
    ``manual_short`` strips ``_Service_Manual``/``_Workshop``
    suffixes and lowercases.  Sequence is zero-padded to 3
    digits.
    """
    short = (
        manual_id
        .replace("_Service_Manual", "")
        .replace("_Workshop_Manual", "")
        .replace("_Workshop", "")
        .lower()
    )
    return f"{short}-{category}-{seq:03d}"


async def generate_candidates(
    manual_id: str,
    category: str,
    count: int,
    model: str,
    manual_dir: Path,
    client: Any,
    rng: Optional[random.Random] = None,
) -> List[Dict[str, Any]]:
    """Generate up to ``count`` grounded candidates.

    Args:
        manual_id: Filename stem (e.g. ``MWS150A_Service_Manual``).
        category: One of the supported categories.
        count: Requested number of candidates.
        model: OpenRouter model ID used for generation.
        manual_dir: Directory containing ``{manual_id}.md``.
        client: ``AsyncOpenAI``-compatible client.
        rng: Optional ``random.Random`` for deterministic tests.

    Returns:
        List of candidate dicts (may be shorter than ``count`` if
        grounding rejected entries).  Each has a ``"id"`` field
        already assigned.
    """
    if category not in _CATEGORIES:
        raise ValueError(
            f"unknown category {category!r}; "
            f"expected one of {sorted(_CATEGORIES)}",
        )

    rng = rng or random.Random()
    md_path = manual_dir / f"{manual_id}.md"
    if not md_path.is_file():
        raise FileNotFoundError(
            f"manual not found: {md_path}",
        )

    md_text = md_path.read_text(encoding="utf-8")
    tree = parse_heading_tree(md_text)
    frontmatter = parse_frontmatter(md_text)

    candidates: List[Dict[str, Any]] = []
    seen_questions: set = set()

    for seq in range(1, count + 1):
        if category == "adversarial":
            toc_sample = _render_toc_sample(tree)
            user_prompt = _user_prompt_adversarial(
                manual_id,
                str(frontmatter.get(
                    "vehicle_model", "unknown",
                )),
                frontmatter.get("page_count", "?"),
                toc_sample,
            )
            system_prompt = _ADVERSARIAL_SYSTEM_PROMPT
            slug = None
            section_text: Optional[str] = None
        else:
            eligible = _filter_sections_for_category(
                md_text, tree, category,
            )
            if not eligible:
                logger.warning(
                    "no eligible sections for category %s",
                    category,
                )
                break
            node = rng.choice(eligible)
            slug = node.slug
            section_raw = extract_section(md_text, slug)
            if not section_raw:
                continue
            section_text = _truncate_section(
                section_raw, _MAX_SECTION_CHARS,
            )
            user_prompt = _user_prompt_grounded(
                manual_id, slug, section_text, category,
            )
            system_prompt = _SYSTEM_PROMPT

        payload = await _call_generator(
            client, model, system_prompt, user_prompt,
        )
        if payload is None:
            logger.warning(
                "seq %d: LLM returned no valid JSON",
                seq,
            )
            continue

        candidate, err = _validate_and_ground(
            payload, manual_id, slug, section_text, category,
        )
        if candidate is None:
            logger.warning(
                "seq %d: grounding rejected — %s",
                seq, err,
            )
            continue

        question = candidate["question"].strip().lower()
        if question in seen_questions:
            logger.info(
                "seq %d: duplicate question, skipping",
                seq,
            )
            continue
        seen_questions.add(question)

        candidate["id"] = _make_candidate_id(
            manual_id, category, len(candidates) + 1,
        )
        candidates.append(candidate)

    return candidates


# ── CLI ───────────────────────────────────────────────────────────


def _build_client(
    api_key: str, base_url: str,
) -> AsyncOpenAI:
    """Construct a default OpenAI-compatible client."""
    return AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
        default_headers={
            "HTTP-Referer": "https://stf-diagnosis.local",
            "X-Title": "STF Golden Generator",
        },
    )


def _write_jsonl(
    path: Path, entries: List[Dict[str, Any]],
) -> None:
    """Append entries to a JSONL file, one object per line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, ensure_ascii=False))
            handle.write("\n")


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate grounded golden candidates for the "
            "manual-agent eval suite."
        ),
    )
    parser.add_argument(
        "--manual", required=True,
        help="Manual filename stem (e.g. MWS150A_Service_Manual)",
    )
    parser.add_argument(
        "--category", required=True, choices=sorted(_CATEGORIES),
    )
    parser.add_argument(
        "--count", type=int, default=10,
        help="Number of candidates to request.",
    )
    parser.add_argument(
        "--model", default=_DEFAULT_MODEL,
        help=(
            "OpenRouter model ID (default: "
            f"{_DEFAULT_MODEL}).  Choose something different "
            "from the judge model to reduce circularity."
        ),
    )
    parser.add_argument(
        "--out", required=True, type=Path,
        help="Output JSONL path.",
    )
    parser.add_argument(
        "--manual-dir", type=Path,
        default=Path(settings.manual_storage_path),
        help="Directory containing {manual}.md files.",
    )
    parser.add_argument(
        "--seed", type=int,
        help="Optional RNG seed for reproducible sampling.",
    )
    return parser.parse_args(argv)


async def _amain(args: argparse.Namespace) -> int:
    if not settings.premium_llm_api_key:
        logger.error(
            "PREMIUM_LLM_API_KEY is not set.  Export it "
            "and retry.",
        )
        return 2

    client = _build_client(
        settings.premium_llm_api_key,
        settings.premium_llm_base_url,
    )
    rng = random.Random(args.seed) if args.seed else None

    logger.info(
        "generating %d %s candidate(s) for %s using %s",
        args.count, args.category, args.manual, args.model,
    )

    candidates = await generate_candidates(
        manual_id=args.manual,
        category=args.category,
        count=args.count,
        model=args.model,
        manual_dir=args.manual_dir,
        client=client,
        rng=rng,
    )

    if not candidates:
        logger.error("no valid candidates produced")
        return 1

    _write_jsonl(args.out, candidates)
    logger.info(
        "wrote %d candidate(s) to %s",
        len(candidates), args.out,
    )
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
