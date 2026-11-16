"""Issue #288: report manual citations must open what the agent read.

Two live failures (2026-11-15, TRICITY155 manual):

* a ``read_manual_section`` call whose section was ambiguous came back as
  normal text ("… is ambiguous (6 matches). Pick ONE node_id …"), and the
  report still cited the model's guessed id as ``source="trace"``; the
  section endpoint then answered ``409 section_ambiguous``.  Now only a
  call that really read a section is cited, by the unique id it read;
* references written in the report text lost their CJK part
  (``fuel-fault-``) because the slug pattern was ASCII-only.

Author: Xiangzhu Yan
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
import yaml

from stf_v3.diagnosis.agent.deps import ToolCallTrace
from stf_v3.diagnosis.agent.report import extract_citations
from stf_v3.diagnosis.tools import manual_tools
from stf_v3.errors import ApiError
from stf_v3.knowledge import manual_index, service
from stf_v3.settings import settings
from tests.agent_helpers import make_deps, small_manual, stub_ctx

TABLE = "自我診斷功能與診斷代碼表"
CONTENT = (
    f"# {TABLE}\n\n故障代碼 P0117 → 診斷代碼 06。\n\n"
    f"# {TABLE}\n\n診斷代碼 06：冷卻液溫度。\n\n"
    "# 冷卻液溫度感知器的檢查\n\n20°C 時 2510–2770 Ω。\n"
)
NODES: List[Dict[str, Any]] = [
    {"node_id": f"general-spec-{TABLE}", "title": TABLE, "node_type": "spec",
     "subsystem": "general", "page_range": [5, 5], "md_lines": [0, 4]},
    {"node_id": f"general-desc-{TABLE}", "title": TABLE, "node_type": "description",
     "subsystem": "general", "page_range": [6, 6], "md_lines": [4, 8]},
    {"node_id": "general-insp-冷卻液溫度感知器的檢查", "title": "冷卻液溫度感知器的檢查",
     "node_type": "inspection", "subsystem": "general", "page_range": [7, 7], "md_lines": [8, 11]},
]


@pytest.fixture()
def indexed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """An indexed manual with two sections of the same title (as TRICITY155)."""
    monkeypatch.setattr(settings, "manual_storage_path", str(tmp_path))
    monkeypatch.setattr(manual_index, "_MANUAL_DIR", tmp_path)
    monkeypatch.delenv("MANUAL_INDEX_TRACK", raising=False)
    manual_index._cache.clear()  # noqa: SLF001
    mid = uuid.uuid4()
    directory = tmp_path / "TRICITY155" / "index"
    directory.mkdir(parents=True)
    md = directory / f"{mid}.md"
    md.write_text(CONTENT, encoding="utf-8")
    tree = [dict(n, aliases=[], summary="", children=[]) for n in NODES]
    (directory / f"{mid}.index.yaml").write_text(yaml.safe_dump({
        "manual_id": str(mid),
        "source": {"content_file": md.name,
                   "content_sha256": hashlib.sha256(md.read_bytes()).hexdigest()},
        "faults": [], "tree": tree,
    }, allow_unicode=True), encoding="utf-8")
    rel = str(md.relative_to(tmp_path))
    deps = make_deps(manual_root=tmp_path, manuals=[small_manual(str(mid), md_file_path=rel)])
    row = SimpleNamespace(id=mid, status="ingested", md_file_path=rel)
    return SimpleNamespace(mid=str(mid), deps=deps, row=row)


async def test_only_real_reads_are_trace_citations_and_they_open(indexed: SimpleNamespace) -> None:
    """A guessed id that is ambiguous is never a trace citation; a read by
    node id or by unique title is cited by its node id, and the browser's
    section endpoint opens every trace citation (the tool read it, so the
    page must open it)."""
    mid, deps = indexed.mid, indexed.deps
    guessed = await manual_tools.read_manual_section(
        stub_ctx(deps, "c1"), manual_id=mid, section=f"electrical-fault-{TABLE}")
    by_id = await manual_tools.read_manual_section(
        stub_ctx(deps, "c2"), manual_id=mid, section=f"general-spec-{TABLE}")
    by_title = await manual_tools.read_manual_section(
        stub_ctx(deps, "c3"), manual_id=mid, section="冷卻液溫度感知器的檢查")
    assert "ambiguous (2 matches)" in guessed
    assert "P0117" in by_id and "2510" in by_title
    assert [t.cite_ref for t in deps.trace] == [
        None, f"{mid}#general-spec-{TABLE}", f"{mid}#general-insp-冷卻液溫度感知器的檢查"]

    text = (f"見 `{mid}#electrical-fault-{TABLE}`、`{mid}#general-spec-{TABLE}` "
            f"與 `{mid}#冷卻液溫度感知器的檢查`。")
    cits = extract_citations(text, deps.trace, [mid])
    assert [(c.ref, c.source) for c in cits] == [
        (f"{mid}#general-spec-{TABLE}", "trace"),
        (f"{mid}#general-insp-冷卻液溫度感知器的檢查", "trace"),
        (f"{mid}#electrical-fault-{TABLE}", "NO_SOURCE"),   # asked, never read
    ]
    for c in cits:
        if c.source == "trace":
            section = service.read_section(indexed.row, c.ref.split("#", 1)[1])
            assert section.section_id == c.ref.split("#", 1)[1]
    with pytest.raises(ApiError) as err:
        service.read_section(indexed.row, f"electrical-fault-{TABLE}")
    assert err.value.code == "section_ambiguous"


async def test_markdown_track_cites_the_canonical_slug_once() -> None:
    """Without an index: a title read is cited by its slug, a miss is not
    cited, and a repeated read yields one citation."""
    deps = make_deps()
    await manual_tools.read_manual_section(stub_ctx(deps, "c1"), manual_id="m1", section="Fuel Pump Troubleshooting")
    await manual_tools.read_manual_section(stub_ctx(deps, "c2"), manual_id="m1", section="Turbocharger")
    await manual_tools.read_manual_section(stub_ctx(deps, "c3"), manual_id="m1", section="Fuel Pump Troubleshooting")
    assert [t.cite_ref for t in deps.trace] == ["m1#2-1-fuel-pump-troubleshooting", None, None]
    cits = extract_citations("see `m1#Fuel Pump Troubleshooting`", deps.trace, ["m1"])
    assert [(c.ref, c.source) for c in cits] == [("m1#2-1-fuel-pump-troubleshooting", "trace")]


def test_error_calls_without_a_read_are_not_cited() -> None:
    """A trace entry with no ``cite_ref`` (ambiguous, not found, blocked or
    an older trace) never becomes a trace citation."""
    trace = [ToolCallTrace(name="read_manual_section", input={"manual_id": "m1", "section": "x"}, latency_ms=1.0),
             ToolCallTrace(name="read_manual_section", input={"manual_id": "m1", "section": "y"}, latency_ms=1.0,
                           is_error=True, cite_ref="m1#y")]
    assert extract_citations("no refs", trace, ["m1"]) == []


@pytest.mark.parametrize("text, refs", [
    ("- `m1#fuel-fault-故障代碼編號-p0117、p0118` — 流程", ["m1#fuel-fault-故障代碼編號-p0117、p0118"]),
    ("參考手冊 `m1#general-insp-冷卻液溫度感知器的檢查` 項目 1", ["m1#general-insp-冷卻液溫度感知器的檢查"]),
    ("規格見 m1#general-spec-自我診斷功能與診斷代碼表-2。", ["m1#general-spec-自我診斷功能與診斷代碼表-2"]),
    ("m1#fuel-fault-p0112、m1#fuel-fault-p0117，", ["m1#fuel-fault-p0112", "m1#fuel-fault-p0117"]),
    ("(m1#3-1-battery).", ["m1#3-1-battery"]),
    ("other `X9#fuel-fault-故障代碼` only", []),   # unknown manual id
])
def test_text_references_keep_their_cjk_slug(text: str, refs: List[str]) -> None:
    """Text references are NO_SOURCE marks with the whole slug: backticked
    ones verbatim, bare ones up to trailing punctuation or the next ref."""
    cits = extract_citations(text, [], ["m1"])
    assert [(c.ref, c.source) for c in cits if c.kind == "manual"] == [(r, "NO_SOURCE") for r in refs]
