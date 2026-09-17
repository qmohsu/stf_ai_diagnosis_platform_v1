"""Manual navigation tools (copied from V2 ``manual_tools.py``).

- ``list_manuals``        — inventory (from the ``manuals`` table via deps,
  never a directory scan; flags whether each manual matches the vehicle).
- ``get_manual_toc``      — heading tree + DTC quick index (index track
  when the sidecar exists).
- ``read_manual_section`` — full section text; images only when
  ``images_enabled`` (FM-42), otherwise ``[image: … omitted]`` markers.
- ``search_manual_text``  — literal grep with enclosing section slug.

``manual_id`` is the manual's database id (the index sidecars are keyed
by it — FM-39).

Author: Xiangzhu Yan
"""

from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from pydantic_ai import BinaryContent, RunContext, ToolReturn

from stf_v3.diagnosis.agent.deps import DiagDeps, ManualInfo, SubAgentDeps, ToolCallTrace, core_deps
from stf_v3.diagnosis.tools._common import execute
from stf_v3.knowledge import manual_fs, manual_index
from stf_v3.knowledge.manual_fs import (
    HeadingNode,
    build_multimodal_section,
    extract_section,
    find_closest_slug,
    parse_heading_tree,
    slugify,
)

_MATCH_SEPARATOR_RE = re.compile(r"[-_\s]+")
_MIN_MATCH_TOKEN_CHARS = 4
_IMAGE_REF_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_HIT_PREVIEW_CHARS = 110
_UNINDEXED_MARKER = "NOT-INDEXED(use search_manual_text)"
_DTC_TOKEN_PATTERN = re.compile(r"\b([PCBU]\d[0-9A-F]{3})\b", re.IGNORECASE)
_DTC_ROW_PATTERN = re.compile(r"^\|\s*([PCBU]\d[0-9A-F]{3})\s*\|", re.IGNORECASE)


# ── Vehicle ↔ manual matching (FM-6) ─────────────────────────────


def normalise_for_match(text: str) -> str:
    """Lower-case, separators stripped (``MWS-150-A`` → ``mws150a``)."""
    return _MATCH_SEPARATOR_RE.sub("", (text or "").lower())


def manual_matches_vehicle(manual: ManualInfo, manufacturer: str, model: str) -> bool:
    """Manufacturer equal + model tokens contained either way.

    ``Corolla`` matches ``Corolla E11``; ``Tricity 155`` matches
    ``TRICITY155``; ``Hiace`` matches neither seed manual.
    """
    if normalise_for_match(manual.manufacturer) != normalise_for_match(manufacturer):
        return False
    a, b = normalise_for_match(manual.vehicle_model), normalise_for_match(model)
    if not a or not b:
        return False
    if len(min(a, b, key=len)) < _MIN_MATCH_TOKEN_CHARS:
        return a == b
    return a in b or b in a


# ── Guard hooks (manual sub-agent only; FM-40) ───────────────────


def _guard(ctx: RunContext[Any]) -> Any:
    state = getattr(ctx.deps, "state", None)
    return state if state is not None and hasattr(state, "check_access") else None


def _blocked(ctx: RunContext[Any], name: str, args: Dict[str, Any]) -> Optional[str]:
    """Blocked message for a foreign-manual read, recorded as an error call."""
    guard = _guard(ctx)
    if guard is None:
        return None
    message = guard.check_access(str(args.get("manual_id", "")))
    if message is None:
        return None
    core = core_deps(ctx.deps)
    entry = ToolCallTrace(name=name, input=dict(args), latency_ms=0.0, is_error=True,
                          tool_call_id=ctx.tool_call_id, output_chars=len(message))
    ctx.deps.trace.append(entry)
    core.trace.append(entry)
    return message


def _after(ctx: RunContext[Any], name: str, args: Dict[str, Any]) -> None:
    guard = _guard(ctx)
    if guard is not None:
        guard.after_call(name, args)


# ── File access ──────────────────────────────────────────────────


def _md_text(core: DiagDeps, manual: ManualInfo) -> Optional[str]:
    if not manual.md_file_path:
        return None
    path = Path(core.manual_root) / manual.md_file_path
    if not path.is_file():
        return None
    return manual_fs._clean_md(manual_fs.promote_unheaded_titles(path.read_text(encoding="utf-8")))


def _manual_dir(core: DiagDeps, manual: ManualInfo) -> Path:
    if manual.md_file_path:
        return (Path(core.manual_root) / manual.md_file_path).parent
    return Path(core.manual_root)


def _not_found(core: DiagDeps, manual_id: str) -> str:
    available = ", ".join(m.id for m in core.manuals)
    if available:
        return f"Manual '{manual_id}' not found. Available manuals: {available}"
    return f"Manual '{manual_id}' not found. No manuals are available."


# ── list_manuals ─────────────────────────────────────────────────


async def list_manuals(ctx: RunContext[Any], vehicle_model: Optional[str] = None) -> str:
    """List available service manuals.

    Returns manual IDs, vehicle models, page counts, section counts and
    whether each manual matches the vehicle under diagnosis. Use
    vehicle_model to filter for a specific vehicle. Call this first to
    discover what manuals are available before using get_manual_toc or
    read_manual_section.

    Args:
        vehicle_model: Filter by vehicle model (e.g. 'MWS-150-A'). Omit to
            list all available manuals.
    """
    result = await execute(ctx, "list_manuals", {"vehicle_model": vehicle_model}, lambda: _list_manuals(ctx, vehicle_model))
    guard = _guard(ctx)
    if guard is not None:
        guard.on_list_manuals()
    return result


async def _list_manuals(ctx: RunContext[Any], vehicle_filter: Optional[str]) -> str:
    core = core_deps(ctx.deps)
    if not core.manuals:
        return "No manuals found in storage. Upload a service manual PDF first."
    entries: List[str] = []
    for m in core.manuals:
        code = m.factory_code or ""
        if vehicle_filter:
            vf = vehicle_filter.lower()
            if (vf not in m.vehicle_model.lower() and vf not in m.manufacturer.lower()
                    and vf not in m.canonical.lower() and vf not in code.lower()):
                continue
        match = manual_matches_vehicle(m, core.vehicle.manufacturer, core.vehicle.model)
        code_part = f'factory_code="{code}"  ' if code else ""
        entries.append(
            f'- {m.id}  vehicle="{m.canonical}"  {code_part}'
            f"pages={m.page_count if m.page_count is not None else '?'}  "
            f"sections={m.section_count if m.section_count is not None else '?'}  "
            f"matches_this_vehicle={'yes' if match else 'no'}"
        )
    if not entries:
        if vehicle_filter:
            return (f"No manuals found matching '{vehicle_filter}'. Use list_manuals without a filter "
                    f"to see all available manuals.")
        return "No manuals found in storage."
    header = f"Available manuals ({len(entries)}):\n"
    footer = (
        "\n\nIMPORTANT: only treat a manual as authoritative for this diagnosis if its `vehicle=` "
        "make/model OR its `factory_code=` matches the vehicle under investigation "
        f"({core.vehicle.manufacturer} {core.vehicle.model}); `matches_this_vehicle=yes` marks the "
        "manuals whose make/model match the vehicle record. A manual's `factory_code` is an "
        "alternate identifier for the SAME vehicle (e.g. factory_code=\"MWS150-A\" is the Yamaha "
        "Tricity 155). If none of the manuals above match this vehicle, say so explicitly — e.g. "
        "\"no service manual is available for this vehicle\" — and do NOT adopt an unrelated "
        "manual's vehicle identity or use it as ground truth."
    )
    return header + "\n".join(entries) + footer


# ── get_manual_toc ───────────────────────────────────────────────


def _format_toc_tree(nodes: List[HeadingNode], indent: int = 0, max_depth: Optional[int] = None) -> str:
    lines: List[str] = []
    prefix = "  " * indent
    for node in nodes:
        lines.append(f"{prefix}- {node.title}  [{node.slug}]")
        if not node.children:
            continue
        if max_depth is not None and indent + 1 >= max_depth:
            hidden = _count_descendants(node.children)
            if hidden > 0:
                lines.append(f"{prefix}  ...{hidden} more nested sections (call get_manual_toc with "
                             f"max_depth={max_depth + 1} or higher)")
            continue
        lines.append(_format_toc_tree(node.children, indent + 1, max_depth=max_depth))
    return "\n".join(lines)


def _count_descendants(nodes: List[HeadingNode]) -> int:
    return sum(1 + _count_descendants(n.children) for n in nodes)


def _flatten(nodes: List[HeadingNode]) -> List[HeadingNode]:
    out: List[HeadingNode] = []
    for node in nodes:
        out.append(node)
        out.extend(_flatten(node.children))
    return out


def _build_dtc_slug_map(md_text: str) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for node in _flatten(parse_heading_tree(md_text)):
        for raw in _DTC_TOKEN_PATTERN.findall(node.title):
            mapping.setdefault(raw.upper(), node.slug)
    return mapping


def _augment_dtc_index(index_text: str, slug_map: Dict[str, str]) -> str:
    out: List[str] = []
    for line in index_text.split("\n"):
        stripped = line.rstrip()
        row = _DTC_ROW_PATTERN.match(stripped)
        if row:
            out.append(f"{stripped} {slug_map.get(row.group(1).upper(), _UNINDEXED_MARKER)} |")
        elif re.match(r"^\|[\s:|-]+\|$", stripped):
            out.append(f"{stripped}-----|")
        elif stripped.startswith("|"):
            out.append(f"{stripped} Section slug |")
        else:
            out.append(line)
    return "\n".join(out)


def _extract_dtc_index(md_text: str) -> Optional[str]:
    section = extract_section(md_text, slugify("Appendix: DTC Index"))
    if section is None:
        return None
    body = "\n".join(l for l in section.split("\n") if not l.startswith("#")).strip()
    return body or None


async def get_manual_toc(ctx: RunContext[Any], manual_id: str, max_depth: int = 3) -> str:
    """Get the table of contents (heading structure) of a specific manual.

    Returns section titles with their slugs and a DTC quick-reference
    index. Use this to find the right section slug before calling
    read_manual_section. Requires manual_id from list_manuals.

    Args:
        manual_id: Manual id (from list_manuals). Use list_manuals to
            discover available IDs.
        max_depth: Cap on how deep the heading tree goes (1 = chapters only,
            3 = chapters + sections + subsections, 99 = full tree). Default 3
            keeps the response small enough to fit in a typical context
            budget; pass a higher value to drill in.
    """
    args = {"manual_id": manual_id, "max_depth": max_depth}
    blocked = _blocked(ctx, "get_manual_toc", args)
    if blocked:
        return blocked
    result = await execute(ctx, "get_manual_toc", args, lambda: _get_manual_toc(ctx, manual_id, max_depth))
    _after(ctx, "get_manual_toc", args)
    return result


async def _get_manual_toc(ctx: RunContext[Any], manual_id: str, max_depth_raw: Any) -> str:
    core = core_deps(ctx.deps)
    try:
        max_depth: Optional[int] = int(max_depth_raw) if max_depth_raw else None
    except (TypeError, ValueError):
        max_depth = 3
    manual = core.manual_by_id(manual_id)
    if manual is None:
        return _not_found(core, manual_id)
    rt_index = manual_index.load_runtime_index(manual_id)
    if rt_index is not None:
        depth = max_depth or 3
        toc = rt_index.toc_text(max_depth=depth)
        while len(toc) > 60_000 and depth > 1:
            depth -= 1
            toc = rt_index.toc_text(max_depth=depth)
            toc += (f"\n\n(large manual: tree auto-reduced to depth {depth} — pass max_depth={depth + 1} "
                    f"or read a parent node_id to see nested sections)")
        return toc
    md_text = _md_text(core, manual)
    if md_text is None:
        return _not_found(core, manual_id)
    tree = parse_heading_tree(md_text)
    if not tree:
        return f"Manual '{manual_id}' has no headings. The file may be empty or malformed."
    toc = _format_toc_tree(tree, max_depth=max_depth)
    dtc_index = _extract_dtc_index(md_text)
    if dtc_index:
        toc += ("\n\nDTC Quick Index (pass a Section slug to read_manual_section for the code's diagnostic "
                "procedure):\n" + _augment_dtc_index(dtc_index, _build_dtc_slug_map(md_text))
                + "\n\nIMPORTANT: a row marked NOT-INDEXED(use search_manual_text) means the code IS present "
                "in the manual (see its occurrence count) but no section heading names it — locate its "
                "content with search_manual_text. NEVER cite a NOT-INDEXED mark as evidence the manual "
                "lacks the code.")
    return toc


# ── read_manual_section ──────────────────────────────────────────


def _match_section(query: str, available_slugs: List[str]) -> Optional[str]:
    if query in available_slugs:
        return query
    query_slug = slugify(query)
    if query_slug in available_slugs:
        return query_slug
    for slug in available_slugs:
        if query_slug and query_slug in slug:
            return slug
    return None


def strip_images(section_text: str) -> Tuple[str, int]:
    """Replace image references with ``[image: alt omitted]`` markers."""
    count = 0

    def _sub(m: "re.Match[str]") -> str:
        nonlocal count
        count += 1
        alt = m.group(1).strip() or f"figure {count}"
        return f"[image: {alt} omitted]"

    return _IMAGE_REF_RE.sub(_sub, section_text), count


def blocks_to_content(blocks: List[Dict[str, Any]]) -> List[Union[str, BinaryContent]]:
    """V2 content blocks → Pydantic AI user content (text + BinaryContent)."""
    out: List[Union[str, BinaryContent]] = []
    for block in blocks:
        if block.get("type") == "text":
            out.append(block.get("text", ""))
        elif block.get("type") == "image_url":
            url = block["image_url"]["url"]
            header, b64 = url.split(",", 1)
            media_type = header[len("data:"):].split(";", 1)[0]
            out.append(BinaryContent(data=base64.b64decode(b64), media_type=media_type))
    return out


async def read_manual_section(
    ctx: RunContext[Any],
    manual_id: str,
    section: str,
    include_subsections: bool = True,
) -> Union[str, ToolReturn]:
    """Read a specific section from a service manual by heading slug or title text.

    Returns the full section content (embedded images — wiring diagrams,
    exploded views, diagnostic flowcharts — are included when image
    delivery is enabled, otherwise marked as omitted). Use get_manual_toc
    first to find section slugs. Accepts both exact slugs (e.g.
    '3-2-fuel-system-troubleshooting') and heading text (e.g. 'Fuel
    System Troubleshooting').

    Args:
        manual_id: Manual id (from list_manuals).
        section: Section heading text or slug (e.g.
            '3-2-fuel-system-troubleshooting' or 'Fuel System
            Troubleshooting'). Use get_manual_toc to find available sections.
        include_subsections: Include child subsections in the result.
    """
    args = {"manual_id": manual_id, "section": section, "include_subsections": include_subsections}
    blocked = _blocked(ctx, "read_manual_section", args)
    if blocked:
        return blocked
    text = await execute(ctx, "read_manual_section", args,
                         lambda: _read_manual_section_text(ctx, manual_id, section, include_subsections))
    _after(ctx, "read_manual_section", args)
    core = core_deps(ctx.deps)
    _record_section(ctx, manual_id, section, text)
    if core.images_enabled and _IMAGE_REF_RE.search(text):
        manual = core.manual_by_id(manual_id)
        if manual is not None:
            blocks = build_multimodal_section(text, _manual_dir(core, manual))
            content = blocks_to_content(blocks)
            if any(isinstance(c, BinaryContent) for c in content):
                stripped, _ = strip_images(text)
                return ToolReturn(return_value=stripped, content=content)
    return text


async def _read_manual_section_text(ctx: RunContext[Any], manual_id: str, section_query: str, include_subs: bool) -> str:
    core = core_deps(ctx.deps)
    manual = core.manual_by_id(manual_id)
    if manual is None:
        return _not_found(core, manual_id)
    rt_index = manual_index.load_runtime_index(manual_id)
    if rt_index is not None:
        node, candidates = rt_index.resolve(section_query)
        if node is None and candidates:
            listing = "\n".join(f"- {c.title}  [{c.node_id}] ({c.subsystem}/{c.node_type})" for c in candidates[:10])
            return (f"Section query '{section_query}' is ambiguous ({len(candidates)} matches). "
                    f"Pick ONE node_id and call again:\n{listing}")
        if node is None:
            return (f"Section '{section_query}' not found in manual '{manual_id}'. Use get_manual_toc for "
                    f"node_ids or search_manual_text to locate content.")
        section_text = rt_index.section_text(node)
        if section_text is None:
            return f"Node '{node.node_id}' has no content anchor — use search_manual_text."
        return _maybe_strip(core, section_text)
    md_text = _md_text(core, manual)
    if md_text is None:
        return _not_found(core, manual_id)
    flat = _flatten(parse_heading_tree(md_text))
    all_slugs = [n.slug for n in flat]
    target = _match_section(section_query, all_slugs)
    if target is None:
        closest = find_closest_slug(section_query, all_slugs)
        if closest:
            title = next((n.title for n in flat if n.slug == closest), closest)
            return (f"Section '{section_query}' not found in manual '{manual_id}'. Did you mean: '{title}' "
                    f"(slug: {closest})? Use get_manual_toc to see all sections.")
        return (f"Section '{section_query}' not found in manual '{manual_id}'. Use get_manual_toc to see "
                f"available sections and their slugs.")
    section_text = extract_section(md_text, target, include_subs)
    if section_text is None:
        return f"Could not extract section '{target}' from manual '{manual_id}'."
    return _maybe_strip(core, section_text)


def _maybe_strip(core: DiagDeps, section_text: str) -> str:
    if core.images_enabled:
        return section_text
    stripped, _ = strip_images(section_text)
    return stripped


def resolve_section_slug(core: DiagDeps, manual_id: str, raw_slug: str) -> str:
    """Canonical slug / node id for a section reference (V2 ``_canonicalise_slug``)."""
    rt_index = manual_index.load_runtime_index(manual_id)
    if rt_index is not None:
        node, _ = rt_index.resolve(raw_slug)
        return node.node_id if node is not None else raw_slug
    manual = core.manual_by_id(manual_id)
    md_text = _md_text(core, manual) if manual else None
    if md_text is None:
        return raw_slug
    known = [n.slug for n in _flatten(parse_heading_tree(md_text))]
    if raw_slug in known:
        return raw_slug
    slugified = slugify(raw_slug)
    if slugified in known:
        return slugified
    if slugified:
        for slug in known:
            if slugified in slug:
                return slug
    return raw_slug


def _record_section(ctx: RunContext[Any], manual_id: str, section: str, text: str) -> None:
    """Keep read sections on the sub-agent deps (evidence for its result)."""
    deps = ctx.deps
    if not isinstance(deps, SubAgentDeps):
        return
    if text.startswith("Section ") or text.startswith("Manual ") or text.startswith("Error:") or text.startswith("Node "):
        return
    from stf_v3.diagnosis.agent.types import SectionRef  # local import: types → no cycle
    canonical = resolve_section_slug(deps.parent, manual_id, section)
    deps.raw_sections.append(SectionRef(
        manual_id=manual_id, slug=canonical, text=text,
        had_images="[image:" in text or bool(_IMAGE_REF_RE.search(text)),
    ))


# ── search_manual_text ───────────────────────────────────────────


def _low_value(line: str) -> bool:
    return bool(re.search(r"\.{3,}", line)) or "參閱" in line


async def search_manual_text(ctx: RunContext[Any], manual_id: str, query: str, max_hits: int = 20) -> str:
    """Literal (grep-style) full-text search over one manual.

    Case-insensitive substring match; each hit shows the matching line and
    its enclosing section slug for read_manual_section. Use for identifier
    lookups (DTC codes, part names, spec labels) and ALWAYS before
    concluding the manual does not contain something — 'not in the TOC'
    or a NOT-INDEXED quick-index mark is NOT evidence of absence; zero
    matches here is.

    Args:
        manual_id: Manual id (from list_manuals).
        query: Literal text to find (case-insensitive substring), e.g.
            'P0335' or '曲軸位置感知器'. Not a regex, not a semantic query.
        max_hits: Cap on returned matching lines (default 20). The total
            match count is always reported.
    """
    args = {"manual_id": manual_id, "query": query, "max_hits": max_hits}
    result = await execute(ctx, "search_manual_text", args, lambda: _search_manual_text(ctx, manual_id, query, max_hits))
    _after(ctx, "search_manual_text", args)
    return result


async def _search_manual_text(ctx: RunContext[Any], manual_id: str, query: str, max_hits: int) -> str:
    core = core_deps(ctx.deps)
    max_hits = max(1, min(int(max_hits), 100))
    if len((query or "").strip()) < 2:
        return "Validation error: `query` must be at least 2 characters."
    manual = core.manual_by_id(manual_id)
    if manual is None:
        return _not_found(core, manual_id)
    needle = query.casefold()
    rt_index = manual_index.load_runtime_index(manual_id)
    if rt_index is not None:
        raw_hits = [(i, l.strip()) for i, l in enumerate(rt_index.content_lines) if needle in l.casefold()]
        hits = [h for h in raw_hits if not _low_value(h[1])] + [h for h in raw_hits if _low_value(h[1])]
        if not hits:
            return (f"0 matches for '{query}' in manual '{manual_id}'. The manual does not contain this text "
                    f"(checked all {len(rt_index.content_lines)} lines).")
        shown = hits[:max_hits]
        out = [f"{len(hits)} line(s) match '{query}' in manual '{manual_id}'"
               + (f" (showing first {len(shown)}):" if len(hits) > len(shown) else ":")]
        for idx, text in shown:
            preview = text[:_HIT_PREVIEW_CHARS] + ("…" if len(text) > _HIT_PREVIEW_CHARS else "")
            out.append(f"- [node: {rt_index.enclosing_node_id(idx)}] {preview}")
        out.append("\nPass a node_id above to read_manual_section to read the full context of a hit.")
        return "\n".join(out)
    md_text = _md_text(core, manual)
    if md_text is None:
        return _not_found(core, manual_id)
    lines = md_text.split("\n")
    hits = [(i, l.strip()) for i, l in enumerate(lines) if needle in l.casefold()]
    if not hits:
        return (f"0 matches for '{query}' in manual '{manual_id}'. The manual does not contain this text "
                f"(checked all {len(lines)} lines).")
    starts = [(n.line_start, n.slug) for n in _flatten(parse_heading_tree(md_text))]

    def enclosing(line_idx: int) -> str:
        best = "(before first section)"
        for start, slug in starts:
            if start <= line_idx:
                best = slug
            else:
                break
        return best

    shown = hits[:max_hits]
    out = [f"{len(hits)} line(s) match '{query}' in manual '{manual_id}'"
           + (f" (showing first {len(shown)}):" if len(hits) > len(shown) else ":")]
    for idx, text in shown:
        preview = text[:_HIT_PREVIEW_CHARS] + ("…" if len(text) > _HIT_PREVIEW_CHARS else "")
        out.append(f"- [section: {enclosing(idx)}] {preview}")
    out.append("\nPass a section slug above to read_manual_section to read the full context of a hit.")
    return "\n".join(out)


MANUAL_TOOLS = [list_manuals, get_manual_toc, read_manual_section, search_manual_text]
