"""Unit tests for track-aware golden selection (#234).

The manual lane's locked golden file must follow the
``MANUAL_INDEX_TRACK`` switch: index track (default) grades
against the makeup overlay's node-id anchors, ``off`` grades
against the legacy-slug file.  Getting this wrong silently
collapses ``section_recall`` (the #234 pseudo-regression).

Author: Li-Ta Hsu
"""

from __future__ import annotations

import pytest

from tests.harness.evals.conftest import (
    _LOCKED_INDEXED,
    _LOCKED_LEGACY,
    load_golden,
    resolve_manual_golden_path,
)


def test_default_track_selects_indexed(monkeypatch) -> None:  # noqa: ANN001
    """Unset env (auto) selects the node-id makeup overlay."""
    monkeypatch.delenv("MANUAL_INDEX_TRACK", raising=False)
    assert resolve_manual_golden_path() == _LOCKED_INDEXED


def test_track_off_selects_legacy(monkeypatch) -> None:  # noqa: ANN001
    """MANUAL_INDEX_TRACK=off selects the legacy-slug file."""
    monkeypatch.setenv("MANUAL_INDEX_TRACK", "off")
    assert resolve_manual_golden_path() == _LOCKED_LEGACY


def test_track_on_selects_indexed(monkeypatch) -> None:  # noqa: ANN001
    """Any non-off value selects the indexed overlay."""
    monkeypatch.setenv("MANUAL_INDEX_TRACK", "on")
    assert resolve_manual_golden_path() == _LOCKED_INDEXED


def test_indexed_overlay_parses_and_mirrors_legacy_ids() -> None:
    """The overlay validates and covers the same entry ids.

    The makeup only remaps positional anchors — entry identity and
    count must match the legacy locked file exactly, or the two
    tracks would silently grade different question sets.
    """
    legacy = load_golden(_LOCKED_LEGACY)
    indexed = load_golden(_LOCKED_INDEXED)
    assert {e.id for e in indexed} == {e.id for e in legacy}
    assert len(indexed) == len(legacy)


def test_indexed_anchors_are_node_ids() -> None:
    """Spot-check: overlay anchors carry subsystem-type prefixes."""
    indexed = load_golden(_LOCKED_INDEXED)
    prefixed = [
        slug
        for entry in indexed
        for slug in entry.expected_recall_slugs
        if "-" in slug
    ]
    # The overwhelming majority of makeup anchors are
    # ``{subsystem}-{type}-{slug}`` node ids; an overlay without
    # any would mean the makeup regression re-appeared.
    assert prefixed, "no node-id-shaped anchors in the overlay"
