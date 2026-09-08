#!/usr/bin/env python3
"""Render an Excalidraw JSON file to the repo's flat dark-theme preview SVG.

The V3 design doc embeds ``stf_v3_final_architecture_preview.svg`` and the
doc-diagram sync rule (CLAUDE.md) requires the ``.excalidraw`` source and the
preview to change in the same commit.  This script makes the preview
reproducible instead of hand-edited::

    python docs/diagrams/render_excalidraw.py \
        docs/diagrams/stf_v3_final_architecture.excalidraw \
        docs/diagrams/stf_v3_final_architecture_preview.svg

Supported element types: rectangle, text (free or container-bound), arrow.
Rendering conventions match the previously hand-generated preview: rounded
rectangles (rx=8), dashed strokes as ``8 6``, container text centred with a
bold first line, line height 1.25 x font size.

Author: Xiangzhu Yan
"""

import json
import sys
from typing import Dict, List
from xml.sax.saxutils import escape

_PAD = 60
_LINE_HEIGHT = 1.25
_BASELINE = 0.975


def _text_lines(element: Dict) -> List[str]:
    """Return the text element's lines."""
    return element["text"].split("\n")


def _render_rect(element: Dict) -> str:
    """Render a rectangle element."""
    dash = ' stroke-dasharray="8 6"' if element.get(
        "strokeStyle") == "dashed" else ""
    return (
        f'<rect x="{element["x"]}" y="{element["y"]}" '
        f'width="{element["width"]}" height="{element["height"]}" rx="8" '
        f'fill="{element["backgroundColor"]}" '
        f'stroke="{element["strokeColor"]}" '
        f'stroke-width="{element.get("strokeWidth", 2)}"{dash}/>'
    )


def _render_text(element: Dict, rects: Dict[str, Dict]) -> List[str]:
    """Render a text element (container-bound texts are centred)."""
    size = element["fontSize"]
    line_h = size * _LINE_HEIGHT
    lines = _text_lines(element)
    color = element["strokeColor"]
    container = rects.get(element.get("containerId") or "")
    out: List[str] = []
    if container is not None:
        cx = container["x"] + container["width"] / 2
        block_h = line_h * len(lines)
        top = container["y"] + (container["height"] - block_h) / 2
        for i, line in enumerate(lines):
            y = top + i * line_h + _BASELINE * size
            bold = ' font-weight="600"' if i == 0 else ""
            out.append(
                f'<text x="{cx}" y="{y:.2f}" text-anchor="middle" '
                f'font-size="{size}" fill="{color}"{bold}>'
                f'{escape(line)}</text>'
            )
        return out
    bold = ' font-weight="600"' if size == 15 else ""
    for i, line in enumerate(lines):
        y = element["y"] + i * line_h + _BASELINE * size
        out.append(
            f'<text x="{element["x"]}" y="{y:.2f}" font-size="{size}" '
            f'fill="{color}"{bold}>{escape(line)}</text>'
        )
    return out


def _render_arrow(element: Dict) -> str:
    """Render an arrow element as a polyline with an arrowhead marker."""
    pts = " ".join(
        f'{element["x"] + px:g},{element["y"] + py:g}'
        for px, py in element["points"]
    )
    dash = ' stroke-dasharray="8 6"' if element.get(
        "strokeStyle") == "dashed" else ""
    return (
        f'<polyline points="{pts}" fill="none" '
        f'stroke="{element["strokeColor"]}" '
        f'stroke-width="{element.get("strokeWidth", 2)}"{dash} '
        f'marker-end="url(#ah)"/>'
    )


def render(scene: Dict) -> str:
    """Render an Excalidraw scene dict to an SVG string."""
    elements = [e for e in scene["elements"] if not e.get("isDeleted")]
    rects = {e["id"]: e for e in elements if e["type"] == "rectangle"}
    # Horizontal extent from shapes only: free-text widths are estimates
    # and would otherwise widen the canvas unpredictably.
    # Arrow ``width`` is a magnitude; the real extent comes from ``points``.
    xs: List[float] = []
    x2: List[float] = []
    for e in elements:
        if e["type"] == "arrow":
            px = [e["x"] + p[0] for p in e["points"]]
            xs.append(min(px))
            x2.append(max(px))
        elif e["type"] != "text":
            xs.append(e["x"])
            x2.append(e["x"] + e.get("width", 0))
    ys = [e["y"] for e in elements]
    y2 = [e["y"] + e.get("height", 0) for e in elements]
    min_x, min_y = min(xs) - _PAD, min(ys) - 30
    width = max(x2) - min_x + _PAD + 40
    height = max(y2) - min_y + _PAD
    bg = scene.get("appState", {}).get("viewBackgroundColor", "#121212")
    body: List[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{min_x:g} '
        f'{min_y:g} {width:g} {height:g}" font-family="Segoe UI, '
        f'Microsoft YaHei, sans-serif">',
        '<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        '<path d="M0,0 L10,5 L0,10 z" fill="#ced4da"/></marker></defs>',
        f'<rect x="{min_x:g}" y="{min_y:g}" width="{width:g}" '
        f'height="{height:g}" fill="{bg}"/>',
    ]
    body += [_render_rect(e) for e in elements if e["type"] == "rectangle"]
    body += [_render_arrow(e) for e in elements if e["type"] == "arrow"]
    for e in elements:
        if e["type"] == "text":
            body += _render_text(e, rects)
    body.append("</svg>")
    return "\n".join(body) + "\n"


def main(argv: List[str]) -> int:
    """CLI entry point: render <in.excalidraw> <out.svg>."""
    if len(argv) != 3:
        print(__doc__)
        return 2
    with open(argv[1], encoding="utf-8") as fh:
        scene = json.load(fh)
    with open(argv[2], "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render(scene))
    print(f"wrote {argv[2]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
