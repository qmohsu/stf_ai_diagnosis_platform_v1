"""Unit tests for content composition: images inside table HTML.

Issue #292: MinerU writes figures inside a table as
``<img src="images/<name>">`` in the table's HTML.  ``compose`` emits
that HTML verbatim, so it must also copy the referenced files next to
the standalone figures, or every in-table image is a broken link.
"""

from __future__ import annotations

import pathlib
from typing import List, Tuple

from stf_v3.knowledge.pipeline.authority import BaselineLine
from stf_v3.knowledge.pipeline.compose import compose
from stf_v3.knowledge.pipeline.stream import ItemKind, NormalizedItem


class _OnePageAuthority:
    """Minimal text authority: one page, no text layer, no rescues."""

    page_count = 1

    def content_lines(self, page: int) -> List[BaselineLine]:
        """Returns no authoritative lines, so no backfill block."""
        return []

    def lines_in_region(
        self, page: int, bbox: Tuple[float, float, float, float],
    ) -> List[BaselineLine]:
        """Returns no region lines."""
        return []


def _engine(tmp_path: pathlib.Path, names: List[str]) -> pathlib.Path:
    """Creates a fake MinerU output dir holding ``images/<name>``."""
    engine = tmp_path / "engine"
    (engine / "images").mkdir(parents=True)
    for name in names:
        (engine / "images" / name).write_bytes(name.encode())
    return engine


def _table(html: str) -> NormalizedItem:
    """Builds one table stream item on page 1."""
    return NormalizedItem(idx=0, page=1, kind=ItemKind.TABLE, html=html)


def test_compose_copies_images_referenced_inside_table_html(
    tmp_path: pathlib.Path,
) -> None:
    """Each in-table image is copied once; an absent file is recorded."""
    # Arrange
    engine = _engine(tmp_path, ["a.jpg", "b.png"])
    html = (
        '<table><tr><td><img src="images/a.jpg"/></td>'
        "<td><IMG alt='x' SRC='images/b.png'></td>"
        '<td><img src="images/a.jpg"></td>'
        '<td><img src="images/gone.jpg"/></td></tr></table>'
    )
    out = tmp_path / "out"

    # Act
    result = compose([_table(html)], _OnePageAuthority(), engine, out)

    # Assert
    assert sorted(p.name for p in (out / "images").iterdir()) == [
        "a.jpg", "b.png",
    ]
    assert (out / "images" / "a.jpg").read_bytes() == b"a.jpg"
    assert result.table_images_copied == 2
    assert result.table_images_missing == ["images/gone.jpg"]
    assert html in result.markdown  # table HTML itself is unchanged


def test_compose_ignores_table_image_refs_outside_engine_images(
    tmp_path: pathlib.Path,
) -> None:
    """Sub-paths, dot names and other dirs are never copied or listed."""
    # Arrange
    engine = _engine(tmp_path, ["ok.jpg"])
    (engine / "secret.jpg").write_bytes(b"secret")
    (engine / "images" / "sub").mkdir()
    (engine / "images" / "sub" / "x.jpg").write_bytes(b"x")
    html = (
        '<table><tr>'
        '<td><img src="images/../secret.jpg"></td>'
        '<td><img src="images/sub/x.jpg"></td>'
        '<td><img src="images/.hidden"></td>'
        '<td><img src="../secret.jpg"></td>'
        '<td><img src="/etc/passwd"></td>'
        '<td><img data-src="images/ok.jpg"></td>'
        '</tr></table>'
    )
    out = tmp_path / "out"

    # Act
    result = compose([_table(html)], _OnePageAuthority(), engine, out)

    # Assert
    assert list((out / "images").iterdir()) == []
    assert result.table_images_copied == 0
    assert result.table_images_missing == []
