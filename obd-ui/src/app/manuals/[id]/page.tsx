"use client";

import { useEffect, useState } from "react";
import { useRouter, useParams } from "next/navigation";
import { Loader2 } from "lucide-react";
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
  // Defer rendering until after first client-side mount so the
  // SSR HTML and the first CSR pass match exactly.  Without
  // this, params can be undefined on the server pass while
  // present on the client, and the differing decoded manual_id
  // (or downstream `<ManualViewer>` props) trips React #418
  // hydration mismatch.  The loader below is what the server
  // sends; once mounted on the client, we swap to the real
  // viewer.
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    setMounted(true);
  }, []);

  if (!mounted) {
    return (
      <div className="container mx-auto px-4 py-6">
        <div className="flex items-center gap-2 text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading manual...
        </div>
      </div>
    );
  }

  const rawId =
    typeof params?.id === "string" ? params.id : "";
  const manualId = decodeURIComponent(rawId);

  return (
    <div className="container mx-auto px-4 py-6">
      <ManualViewer
        manualId={manualId}
        onBack={() => router.back()}
      />
    </div>
  );
}
