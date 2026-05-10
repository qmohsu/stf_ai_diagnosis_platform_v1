"use client";

import { useRouter, useParams } from "next/navigation";
import { ManualViewer } from "@/components/ManualViewer";

/**
 * Dedicated route for viewing a single manual.
 *
 * The /manuals listing page also shows a viewer (state-toggled)
 * but that flow doesn't support deep-linking from outside the
 * page.  This route lets citations on the golden-review
 * dashboard link straight to a manual section via URL hash:
 *
 *     /manuals/<manual-id>#<slug>
 *
 * `ManualViewer` reads `window.location.hash` after render and
 * scrolls to the matching heading (the heading renderers add
 * `id` attributes via the shared `slugify()` util).
 *
 * Back navigation goes via `router.back()` — preserves the
 * caller's scroll position when they were on the goldens
 * detail page.
 */
export default function ManualDetailPage() {
  const router = useRouter();
  const params = useParams<{ id: string }>();
  const manualId = decodeURIComponent(params.id);

  return (
    <div className="container mx-auto px-4 py-6">
      <ManualViewer
        manualId={manualId}
        onBack={() => router.back()}
      />
    </div>
  );
}
