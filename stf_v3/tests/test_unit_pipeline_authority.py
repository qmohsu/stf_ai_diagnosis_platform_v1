"""Rescued-table text from PyMuPDF's ruled-table finder.

When MinerU drops a table, ``compose`` rescues it with
``TextAuthority.find_table_markdown`` (PyMuPDF ``find_tables`` +
``to_markdown``).  PyMuPDF 1.24.14 garbled that text: hyphens became
``&amp;#45;``, line breaks inside a cell were lost and some cells came
out letter by letter.  The live manuals were built with 1.28.0, which
``pyproject.toml`` now pins.

PyMuPDF is a build-host-only dependency, so these tests skip where it
is not installed (CI) and run on the host GPU worker's venv.
"""

from __future__ import annotations

import pathlib

import pytest

fitz = pytest.importorskip("fitz")

from stf_v3.knowledge.pipeline.authority import TextAuthority  # noqa: E402


def _ruled_table_pdf(path: pathlib.Path) -> None:
    """Writes a one-page PDF with a 2x2 ruled table.

    The first body cell holds two lines, the second a hyphenated code.
    """
    doc = fitz.open()
    page = doc.new_page(width=400, height=300)
    xs, ys = [50, 200, 350], [50, 80, 140]
    for x in xs:
        page.draw_line((x, ys[0]), (x, ys[-1]))
    for y in ys:
        page.draw_line((xs[0], y), (xs[-1], y))
    page.insert_text((55, 70), "Tool")
    page.insert_text((205, 70), "Code")
    page.insert_text((55, 100), "Feeler gauge")
    page.insert_text((55, 120), "YU-26900-9")
    page.insert_text((205, 100), "90890-03180")
    doc.save(str(path))
    doc.close()


def test_rescued_table_keeps_hyphens_and_cell_line_breaks(
    tmp_path: pathlib.Path,
) -> None:
    """Part numbers keep plain hyphens; cell lines join with <br>."""
    # Arrange
    pdf = tmp_path / "table.pdf"
    _ruled_table_pdf(pdf)
    authority = TextAuthority(pdf)

    # Act
    md = authority.find_table_markdown(1, (0, 0, 1000, 1000))
    authority.close()

    # Assert
    assert "Feeler gauge<br>YU-26900-9" in md
    assert "90890-03180" in md
    assert "&#45;" not in md and "&amp;" not in md
