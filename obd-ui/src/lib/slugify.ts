/**
 * Frontend port of `diagnostic_api/app/harness_tools/manual_fs.slugify`.
 *
 * Converts heading text to a stable slug that matches the slugs
 * stored in `golden_citations[].slug` and the slugs the backend
 * uses to address sections.  Same regex semantics: keep ASCII
 * alphanumerics + hyphens + the main CJK Unicode blocks
 * (U+2E80 – U+9FFF, U+F900 – U+FAFF), replace anything else with
 * a single hyphen, strip leading/trailing hyphens.
 *
 * Used by ManualViewer to generate `id` attributes on rendered
 * headings so citations can deep-link into the manual via URL
 * hash (e.g., `/manuals/<id>#故障代碼編號-p0117、p0118`).
 *
 * Caveat: does NOT replicate the duplicate-suffix logic
 * (``-2``, ``-3``) that `parse_heading_tree` applies on the
 * backend.  For the rare entry where two headings slugify to
 * the same string, the URL hash will land on the FIRST
 * occurrence; user can scroll manually if needed.  Not a
 * blocker for the workshop-review use case.
 */
export function slugify(title: string): string {
  // Lowercase first.
  let slug = title.toLowerCase();
  // Replace anything outside the kept set with a single hyphen.
  // Range U+2E80..U+9FFF covers CJK Radicals + CJK Unified
  // Ideographs (incl. CJK Symbols and Punctuation U+3000-303F
  // — which is why `、`/`。` survive).  U+F900..U+FAFF is the
  // CJK Compatibility Ideographs block.
  slug = slug.replace(/[^a-z0-9⺀-鿿豈-﫿-]+/g, "-");
  // Strip leading/trailing hyphens.
  slug = slug.replace(/^-+|-+$/g, "");
  return slug;
}
