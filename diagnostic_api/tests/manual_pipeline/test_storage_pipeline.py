"""Tests for the Phase-1 storage pipeline (HARNESS-30).

Builds a tiny synthetic PDF + MinerU-shaped content list and runs
the full compose → reconcile path, exercising all three guarantees:
engine-ordered emission, region rescue, and the completeness sweep.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

fitz = pytest.importorskip("fitz")

from manual_pipeline.audit import (  # noqa: E402
    ReconciliationError,
    gate_or_raise,
    reconcile,
)
from manual_pipeline.authority import TextAuthority  # noqa: E402
from manual_pipeline.build import (  # noqa: E402
    extract_frontmatter,
    run_build,
)
from manual_pipeline.compose import compose  # noqa: E402
from manual_pipeline.stream import (  # noqa: E402
    ItemKind,
    load_mineru_stream,
)

PAGE_W, PAGE_H = 595, 842

# Page 1 layout: title, paragraph, and a "table" region the engine
# will report EMPTY (rescue trigger).  Page 2: a paragraph the
# engine DROPS entirely (completeness-sweep trigger).
P1_TITLE = "第一章 引擎規格"
P1_PARA = "引擎為水冷四行程單缸,排氣量 155 cc。"
P1_TABLE_ROW = "汽門間隙 進氣 0.10 mm 排氣 0.20 mm"
P2_DROPPED = "扭力值 10 Nm 適用於汽缸頭螺栓。"


@pytest.fixture()
def pdf_path(tmp_path: Path) -> Path:
    """Create a 2-page synthetic PDF with a CJK-capable font."""
    doc = fitz.open()
    p1 = doc.new_page(width=PAGE_W, height=PAGE_H)
    p1.insert_text((50, 80), P1_TITLE, fontname="china-s")
    p1.insert_text((50, 140), P1_PARA, fontname="china-s")
    p1.insert_text((60, 420), P1_TABLE_ROW, fontname="china-s")
    p2 = doc.new_page(width=PAGE_W, height=PAGE_H)
    p2.insert_text((50, 100), P2_DROPPED, fontname="china-s")
    out = tmp_path / "mini.pdf"
    doc.save(str(out))
    doc.close()
    return out


@pytest.fixture()
def mineru_dir(tmp_path: Path) -> Path:
    """MinerU-shaped output: content_list_v2.json + images dir."""
    d = tmp_path / "engine_out"
    (d / "images").mkdir(parents=True)
    # Engine proposes: title + para + EMPTY table (page 1);
    # nothing at all for page 2 (dropped paragraph).
    def norm_box(x0, y0, x1, y1):
        return [
            x0 / PAGE_W * 1000, y0 / PAGE_H * 1000,
            x1 / PAGE_W * 1000, y1 / PAGE_H * 1000,
        ]

    pages = [
        [
            {
                "type": "title",
                "content": {
                    "title_content": [
                        {"type": "text", "content": P1_TITLE},
                    ],
                    "level": 2,
                },
                "bbox": norm_box(40, 60, 400, 95),
            },
            {
                "type": "paragraph",
                "content": {
                    "paragraph_content": [
                        {"type": "text", "content": P1_PARA},
                    ],
                },
                "bbox": norm_box(40, 120, 500, 160),
            },
            {
                "type": "table",
                "content": {
                    "image_source": {"path": "images/"},
                    "html": "",
                    "table_caption": [],
                    "table_footnote": [],
                },
                "bbox": norm_box(40, 390, 520, 450),
            },
            {
                "type": "page_number",
                "content": {"content": "1"},
                "bbox": norm_box(280, 800, 310, 820),
            },
        ],
        [],  # page 2: engine dropped everything
    ]
    (d / "mini_content_list_v2.json").write_text(
        json.dumps(pages, ensure_ascii=False), encoding="utf-8",
    )
    return d


class TestMineruAdapter:
    """load_mineru_stream maps engine types to the contract."""

    def test_kinds_and_rescue_detection(
        self, mineru_dir: Path,
    ) -> None:
        """Empty table is flagged for rescue; page_number dropped."""
        items = load_mineru_stream(
            mineru_dir / "mini_content_list_v2.json",
        )
        kinds = [it.kind for it in items]
        assert kinds == [
            ItemKind.TITLE_CANDIDATE,
            ItemKind.PARA,
            ItemKind.TABLE,
        ]
        table = items[2]
        assert table.needs_rescue()
        assert table.image_path is None  # 'images/' is invalid
        assert items[0].text == P1_TITLE


class TestComposeAndReconcile:
    """Full pipeline: rescue + backfill make I0 pass exactly."""

    def test_i0_passes_by_construction(
        self, pdf_path: Path, mineru_dir: Path, tmp_path: Path,
    ) -> None:
        """Engine-dropped table AND page are both recovered."""
        items = load_mineru_stream(
            mineru_dir / "mini_content_list_v2.json",
        )
        authority = TextAuthority(pdf_path)
        out_dir = tmp_path / "out"
        result = compose(items, authority, mineru_dir, out_dir)

        audit = reconcile(result.markdown, authority)
        assert audit.passed, audit.missing_samples
        # The empty table was rescued with a rendered image and
        # its authoritative text attached.
        assert len(result.rescues) == 1
        rec = result.rescues[0]
        assert rec.kind == "table"
        assert rec.text_lines_attached >= 1
        assert (out_dir / "images" / rec.image_file).is_file()
        # Page 2's dropped paragraph came back via the sweep.
        assert result.recovered_lines >= 1
        assert 2 in result.recovered_pages
        assert P2_DROPPED in result.markdown
        authority.close()

    def test_gate_raises_on_missing_content(
        self, pdf_path: Path,
    ) -> None:
        """A markdown missing baseline lines fails the gate."""
        authority = TextAuthority(pdf_path)
        audit = reconcile("---\nempty\n---\n", authority)
        assert not audit.passed
        with pytest.raises(ReconciliationError):
            gate_or_raise(audit)
        authority.close()


class TestBuildCli:
    """End-to-end run_build writes artifacts and the report."""

    def test_run_build_end_to_end(
        self, pdf_path: Path, mineru_dir: Path, tmp_path: Path,
    ) -> None:
        """Artifacts + build report land; gate passes."""
        fm_src = tmp_path / "fm.md"
        fm_src.write_text(
            "---\nvehicle_model: MINI-1\nfactory_code: M1\n---\n"
            "# body\n",
            encoding="utf-8",
        )
        out_dir = tmp_path / "built"
        ok = run_build(
            pdf_path, mineru_dir, out_dir,
            frontmatter_from=fm_src,
        )
        assert ok
        md = (out_dir / "mini.md").read_text(encoding="utf-8")
        assert md.startswith("---")
        assert "vehicle_model: MINI-1" in md
        report = json.loads(
            (out_dir / "build_report.json").read_text(
                encoding="utf-8",
            ),
        )
        assert report["i0_gate"]["passed"] is True
        assert report["rescues"]["count"] == 1

    def test_extract_frontmatter_absent(
        self, tmp_path: Path,
    ) -> None:
        """A file without frontmatter yields ''."""
        p = tmp_path / "plain.md"
        p.write_text("# no frontmatter\n", encoding="utf-8")
        assert extract_frontmatter(p) == ""
