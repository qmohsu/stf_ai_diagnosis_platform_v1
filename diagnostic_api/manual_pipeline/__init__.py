"""Offline manual storage pipeline (HARNESS-30 Phase 1).

Builds the content artifact (``content.md`` + images) from a source
PDF and a MinerU geometry pass, under the authority-separation
contract of ``docs/manual_index_spec.md`` §1.3:

- text authority   = the PDF text layer via PyMuPDF (100% recall by
  definition for born-digital PDFs);
- geometry advisor = MinerU's ``content_list_v2.json`` (region
  types, reading order, table structure) — low-trust, pluggable;
- region rescue    = engine-dropped regions are rendered from the
  PDF at their bbox and inlined;
- reconciliation   = the I0 gate fails the build if ANY baseline
  text line is missing from the final markdown.

This package is build-time tooling: nothing under ``app/`` imports
it, and its heavy dependencies (PyMuPDF) are NOT part of the API
image.  It runs on the build host (the PolyU server) or any
workstation with the optional deps installed.

Author: Li-Ta Hsu
"""

from __future__ import annotations
