"""Tests for app.rag.parser — document parsing into sections."""

import sys
from pathlib import Path

# Allow running from the infra/ directory by adding diagnostic_api to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "diagnostic_api"))

from app.rag.parser import parse_manual, parse_log, parse_document, Section


# ---------------------------------------------------------------------------
# Sample texts (mirrors the real data files)
# ---------------------------------------------------------------------------

SAMPLE_MANUAL = """\
# 2024 STF-850 Owner's Manual

## Section 3.2: Fuel System Troubleshooting

### P0171 - System Too Lean (Bank 1)
This code indicates that the fuel system is running weak or a vacuum leak exists.

**Possible Causes:**
1. Vacuum Leaks
2. Mass Air Flow (MAF) Sensor
3. Fuel Injectors
4. Fuel Pump

### P0300 - Random/Multiple Cylinder Misfire
Indicates misfires detected in multiple cylinders.

**Possible Causes:**
- Worn spark plugs
- Failed ignition coils
- Vacuum leaks
- Low fuel pressure
"""

SAMPLE_LOG = """\
# Maintenance Log - Vehicle VIN1234567890

**Date:** 2025-10-15
**Service:** Routine Maintenance + Check Engine Light
**Technician:** J. Smith

**Observations:**
Customer reported rough idle and check engine light on. Scanned codes: P0171 (System Too Lean).

**Action Taken:**
- Inspected vacuum hoses. Found cracked hose near intake manifold. Replaced hose.
- Cleared codes. Test drove for 10 miles.
"""


# ---------------------------------------------------------------------------
# parse_manual tests
# ---------------------------------------------------------------------------

class TestParseManual:
    def test_sections_count(self):
        """Manual with 1 H1 + 1 H2 + 2 H3 => 4 sections."""
        sections = parse_manual(SAMPLE_MANUAL, "sample_manual.txt")
        assert len(sections) == 4  # H1 title preamble, H2, H3 P0171, H3 P0300

    def test_section_titles(self):
        sections = parse_manual(SAMPLE_MANUAL, "sample_manual.txt")
        titles = [s.title for s in sections]
        assert "2024 STF-850 Owner's Manual" in titles
        assert "Section 3.2: Fuel System Troubleshooting" in titles
        assert "P0171 - System Too Lean (Bank 1)" in titles
        assert "P0300 - Random/Multiple Cylinder Misfire" in titles

    def test_vehicle_model_extracted(self):
        sections = parse_manual(SAMPLE_MANUAL, "sample_manual.txt")
        # Document-level vehicle model should propagate to all sections
        for s in sections:
            assert s.vehicle_model == "STF-850"

    def test_dtc_codes_per_section(self):
        sections = parse_manual(SAMPLE_MANUAL, "sample_manual.txt")
        by_title = {s.title: s for s in sections}

        p0171 = by_title["P0171 - System Too Lean (Bank 1)"]
        assert "P0171" in p0171.dtc_codes

        p0300 = by_title["P0300 - Random/Multiple Cylinder Misfire"]
        assert "P0300" in p0300.dtc_codes

    def test_heading_levels(self):
        sections = parse_manual(SAMPLE_MANUAL, "sample_manual.txt")
        levels = {s.title: s.level for s in sections}
        assert levels["2024 STF-850 Owner's Manual"] == 1
        assert levels["Section 3.2: Fuel System Troubleshooting"] == 2
        assert levels["P0171 - System Too Lean (Bank 1)"] == 3

    def test_body_not_empty(self):
        sections = parse_manual(SAMPLE_MANUAL, "sample_manual.txt")
        for s in sections:
            # Every section (except possibly preamble-only H1) should have a body
            # H2 section may have empty body since content is under H3
            if s.level >= 3:
                assert len(s.body) > 0


# ---------------------------------------------------------------------------
# parse_log tests
# ---------------------------------------------------------------------------

class TestParseLog:
    def test_single_section(self):
        sections = parse_log(SAMPLE_LOG, "sample_log.txt")
        assert len(sections) == 1

    def test_title_from_date_service(self):
        sections = parse_log(SAMPLE_LOG, "sample_log.txt")
        title = sections[0].title
        assert "2025-10-15" in title
        assert "Routine Maintenance" in title

    def test_dtc_extraction(self):
        sections = parse_log(SAMPLE_LOG, "sample_log.txt")
        assert "P0171" in sections[0].dtc_codes

    def test_fallback_title_no_headers(self):
        plain = "Some random log text without date or service headers."
        sections = parse_log(plain, "my_log.txt")
        assert sections[0].title == "my_log"

    def test_vehicle_model_generic_for_log(self):
        """Log without STF model reference should default to Generic."""
        sections = parse_log(SAMPLE_LOG, "sample_log.txt")
        assert sections[0].vehicle_model == "Generic"


# ---------------------------------------------------------------------------
# parse_document auto-detection tests
# ---------------------------------------------------------------------------

class TestParseDocument:
    def test_log_detection(self):
        sections = parse_document(SAMPLE_LOG, "sample_log.txt")
        # Should use parse_log -> single section
        assert len(sections) == 1

    def test_manual_detection(self):
        sections = parse_document(SAMPLE_MANUAL, "sample_manual.txt")
        assert len(sections) >= 3

    def test_unknown_filename_uses_manual(self):
        sections = parse_document(SAMPLE_MANUAL, "readme.md")
        assert len(sections) >= 3


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_text(self):
        sections = parse_manual("", "empty.txt")
        assert len(sections) == 1
        assert sections[0].title == "empty"
        assert sections[0].body == ""

    def test_no_headings(self):
        plain = "Just a paragraph of text with no markdown headings."
        sections = parse_manual(plain, "notes.txt")
        assert len(sections) == 1
        assert sections[0].title == "notes"
        assert sections[0].vehicle_model == "Generic"

    def test_multiple_dtc_codes(self):
        text = "Codes found: P0171 and B1234 and U0100."
        sections = parse_manual(text, "multi.txt")
        codes = sections[0].dtc_codes
        assert "P0171" in codes
        assert "B1234" in codes
        assert "U0100" in codes

    def test_stf_model_variants(self):
        """STF 850, STF-850, STF-1234 should all be normalised."""
        for variant in ["STF 850", "STF-850", "stf850"]:
            text = f"Vehicle: {variant} service record."
            sections = parse_manual(text, "test.txt")
            assert sections[0].vehicle_model == "STF-850"
