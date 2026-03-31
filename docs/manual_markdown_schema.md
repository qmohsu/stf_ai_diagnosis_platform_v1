# Structured Service Manual Markdown Schema (v1.0)

Specification for storing vehicle service manuals as structured `.md`
files for agent-navigated retrieval (GitHub Issue #33, parent #32).

## 1. File Organisation

### 1.1 Directory

Manuals live in a configurable directory (default `/app/data/manuals/`).
Images are stored alongside in a per-manual subdirectory:

```
/app/data/manuals/
  MWS150A_Service_Manual.md
  images/
    MWS150A_Service_Manual/
      p012-1.png
      p012-2.png
      p045-1.png
```

### 1.2 File Naming

- One `.md` file per source PDF.
- Name: `{original_stem}.md` where `{original_stem}` matches the PDF
  filename without extension, preserving case and underscores.
- Example: `MWS150A_Service_Manual.pdf` -> `MWS150A_Service_Manual.md`

### 1.3 Image Naming

Images extracted from the PDF are stored as:

```
images/{manual_stem}/p{page:03d}-{index}.png
```

- `{page:03d}` = zero-padded 3-digit 1-based PDF page number.
- `{index}` = 1-based image index within that page.
- Example: page 12, second image -> `images/MWS150A_Service_Manual/p012-2.png`

---

## 2. YAML Frontmatter

Every manual file begins with a YAML frontmatter block. All fields are
required unless marked optional.

```yaml
---
source_pdf: MWS150A_Service_Manual.pdf
vehicle_model: MWS-150-A
language: zh-CN
translated: true
exported_at: "2026-03-30T12:00:00Z"
page_count: 415
section_count: 47
---
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `source_pdf` | string | Yes | Original PDF filename |
| `vehicle_model` | string | Yes | Normalised model ID (see Section 6) |
| `language` | string | Yes | BCP 47 source language (`en`, `zh-CN`, `zh-TW`, `ja`) |
| `translated` | bool | No | `true` if content was machine-translated to English |
| `exported_at` | string | Yes | ISO 8601 UTC timestamp of conversion |
| `page_count` | int | Yes | Total pages in source PDF |
| `section_count` | int | Yes | Number of `##` chapter headings in this file |

---

## 3. Heading Conventions

Headings encode the document hierarchy. The converter must produce
exactly these levels:

| Level | Markdown | Semantic Role | Source |
|-------|----------|---------------|--------|
| `#` | `# Title` | Document title (exactly one per file) | PDF title page or filename |
| `##` | `## Chapter` | Chapter / top-level system | `Section.level == 1` from PDF parser |
| `###` | `### Section` | Section / subsystem / procedure | `Section.level == 2` from PDF parser |
| `####` | `#### Subsection` | Subsection, DTC group, or step group | `Section.level >= 3` or DTC extraction |

Rules:

- Never skip levels (no `##` followed directly by `####`).
- Every file has exactly one `#` heading (the document title).
- Empty sections (no body text) should be omitted.

---

## 4. Section Anchor IDs

Every heading gets a stable slug-based anchor for use by the
`read_section` tool. Slugs are derived deterministically from heading
text.

### 4.1 Slug Algorithm

```
1. Take the heading text (without the # prefix).
2. Lowercase.
3. Replace runs of non-alphanumeric characters (except hyphens) with
   a single hyphen.
4. Strip leading/trailing hyphens.
5. Truncate to 80 characters (at a hyphen boundary if possible).
6. If duplicate, append -2, -3, etc.
```

### 4.2 Examples

| Heading | Slug |
|---------|------|
| `## Chapter 3: Engine` | `chapter-3-engine` |
| `### 3.2 Fuel System Troubleshooting` | `3-2-fuel-system-troubleshooting` |
| `#### DTC: P0171 — System Too Lean` | `dtc-p0171-system-too-lean` |
| `### Overview` (first) | `overview` |
| `### Overview` (second) | `overview-2` |

Slugs are not written into the `.md` file. They are computed at
runtime by the navigation tools, using this deterministic algorithm.

---

## 5. Content Conventions

### 5.1 Page Markers

HTML comments mark PDF page boundaries for traceability:

```markdown
<!-- page:42 -->
```

- Placed on its own line immediately before content from that page.
- 1-based page number matching the source PDF.
- Used by tools to generate citations like `doc_id#page:42`.

### 5.2 Images

Images are referenced with standard markdown syntax, followed by an
inline vision-generated description:

```markdown
![Fuel injector exploded diagram](images/MWS150A_Service_Manual/p045-1.png)

*Vision description: Exploded view of the fuel injector assembly
showing nozzle (A), O-ring seal (B), pintle valve (C), and solenoid
coil (D). Torque spec callout: 12 N-m for retaining bolt.*
```

Rules:

- Alt text is a concise label (not the full description).
- Vision description follows as an italic paragraph, prefixed with
  `*Vision description:`.
- If no vision model was used, omit the description paragraph.

### 5.3 Tables

Preserve tables as standard markdown pipe tables:

```markdown
| Specification | Value | Unit |
|---------------|-------|------|
| Idle speed | 1300 +/- 100 | rpm |
| Spark plug gap | 0.6-0.7 | mm |
```

- Header row + separator row required.
- Align columns for readability (optional).

### 5.4 DTC Subsections

DTC codes are extracted into dedicated `####` subsections:

```markdown
#### DTC: P0171 — System Too Lean (Bank 1)

**Possible Causes:**
1. Vacuum leaks
2. Faulty MAF sensor
3. Clogged fuel injectors

**Diagnostic Steps:**
1. Check intake manifold for vacuum leaks.
2. Inspect MAF sensor readings at idle.
```

Format: `#### DTC: {code} — {description}`

- DTC code pattern: `[PBCU]\d{4}` (matching existing `DTC_PATTERN`
  in `parser.py`).
- Multiple related DTCs may share a subsection:
  `#### DTC: P0171, P0174 — System Too Lean`

### 5.5 Bold and List Formatting

- Use `**Bold**` for field labels and emphasis.
- Use numbered lists (`1.`) for ordered procedures/steps.
- Use bullet lists (`-`) for unordered items.
- Use blockquotes (`>`) for warnings or important notes.

### 5.6 Cross-References

Internal cross-references use the section slug:

```markdown
See [Fuel System Troubleshooting](#3-2-fuel-system-troubleshooting).
```

---

## 6. Vehicle Model Normalisation

Vehicle model strings in frontmatter and headings must follow the
patterns already defined in `diagnostic_api/app/rag/parser.py`:

| Pattern | Normalised Format | Example |
|---------|-------------------|---------|
| `STF[-\s]?\d{3,4}` | `STF-{digits}` | `STF-850` |
| `MWS[-\s]?\d{2,4}[-\s]?[A-Z]?` | Raw, uppercased | `MWS-150-A` |
| `TRICITY\s*\d{2,3}` | Hyphenated | `TRICITY-155` |
| `NMAX\s*\d{2,3}` | Hyphenated | `NMAX-125` |
| `XMAX\s*\d{2,3}` | Hyphenated | `XMAX-400` |

If no pattern matches, use the model name as-is from the PDF title
page. Never default to `"Generic"` in frontmatter.

---

## 7. DTC Cross-Reference Index (Optional)

For manuals with many DTC codes, an appendix at the end of the file
provides an O(1) lookup index:

```markdown
## Appendix: DTC Index

| DTC | Description | Section |
|-----|-------------|---------|
| P0171 | System Too Lean (Bank 1) | [3.2 Fuel System](#3-2-fuel-system-troubleshooting) |
| P0174 | System Too Lean (Bank 2) | [3.2 Fuel System](#3-2-fuel-system-troubleshooting) |
| P0300 | Random/Multiple Cylinder Misfire | [4.1 Ignition](#4-1-ignition-system) |
```

- One row per unique DTC code found in the document.
- `Section` column links to the containing section via anchor slug.
- Sorted alphabetically by DTC code.

---

## 8. Compatibility with Existing RAG Pipeline

The structured markdown format is designed to coexist with the current
vector RAG pipeline. Field mappings:

| Markdown Schema | RagChunk Column | Notes |
|-----------------|-----------------|-------|
| Filename stem | `doc_id` | Same derivation |
| `vehicle_model` frontmatter | `vehicle_model` | Same normalisation |
| Always `"manual"` | `source_type` | Structured manuals are always type manual |
| Heading text | `section_title` | `##`/`###`/`####` text |
| DTC codes in `####` headings | `metadata_json.dtc_codes` | Same `[PBCU]\d{4}` pattern |
| Image markers in content | `metadata_json.has_image` | Presence of `![` syntax |

The A/B comparison (#32) can use both pipelines simultaneously:
vector RAG reads from `rag_chunks` table, structured MD tools read
from the `/app/data/manuals/` directory.

---

## 9. Constraints and Non-Goals

- This schema covers **output format only**. The PDF-to-markdown
  converter (Phase 1b, #32) is a separate deliverable.
- The schema does not prescribe chunk sizes or embedding strategies.
  Structured manuals are navigated by tools, not chunked.
- OCR quality, translation accuracy, and vision description quality
  are converter concerns, not schema concerns.
