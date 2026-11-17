"""Unit tests for the MinerU stream adapter: caption text.

MinerU gives table / image captions and footnotes as lists of
rich-text dicts (``{'type': 'text', 'content': '...'}``).  The adapter
used ``str()`` on them, so the dict's ``repr`` ended up in the manual
text (102 lines in TRICITY155, 900 in Corolla).
"""

from __future__ import annotations

import json
import pathlib
from typing import List

from stf_v3.knowledge.pipeline.stream import ItemKind, load_mineru_stream


def _load(tmp_path: pathlib.Path, items: List[dict]) -> list:
    """Writes one page of MinerU items and loads it back."""
    path = tmp_path / "x_content_list_v2.json"
    path.write_text(json.dumps([items], ensure_ascii=False), encoding="utf-8")
    return load_mineru_stream(path)


def test_captions_and_footnotes_become_plain_text(
    tmp_path: pathlib.Path,
) -> None:
    """Rich-text dict parts are flattened, never written as their repr."""
    # Arrange
    items = [
        {"type": "table", "content": {
            "html": "<table><tr><td>1</td></tr></table>",
            "table_caption": [{"type": "text", "content": "診斷代碼表"}],
            "table_footnote": [
                {"type": "text", "content": "註\n見 8-27 頁"},
                {"type": "equation_inline", "content": "R=10\\Omega"},
            ],
        }},
        {"type": "image", "content": {
            "image_source": {"path": "images/a.jpg"},
            "image_caption": [{"type": "text", "content": [
                {"type": "text", "content": "前面"},
                {"type": "text", "content": "圖"},
            ]}],
        }},
    ]

    # Act
    table, image = _load(tmp_path, items)

    # Assert
    assert table.kind == ItemKind.TABLE
    assert table.text == "診斷代碼表 註 見 8-27 頁 R=10\\Omega"
    assert image.kind == ItemKind.IMAGE
    assert image.text == "前面圖"
    assert "'type'" not in table.text + image.text


def test_bare_string_caption_is_not_split_into_characters(
    tmp_path: pathlib.Path,
) -> None:
    """A caption given as one string stays whole; empty fields add nothing."""
    # Arrange
    items = [
        {"type": "image", "content": {
            "image_source": {"path": "images/b.jpg"},
            "image_caption": "Fig 1 Fuel pump",
        }},
        {"type": "table", "content": {
            "html": "<table></table>",
            "table_caption": [],
            "table_footnote": None,
        }},
    ]

    # Act
    image, table = _load(tmp_path, items)

    # Assert
    assert image.text == "Fig 1 Fuel pump"
    assert table.text == ""
