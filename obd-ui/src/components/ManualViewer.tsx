"use client";

import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { ArrowLeft, BookOpen, Loader2 } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { getManual } from "@/lib/api";
import type { ManualDetail } from "@/lib/types";

interface ManualViewerProps {
  manualId: string;
  onBack: () => void;
}

/**
 * Strip YAML frontmatter from markdown content.
 *
 * Frontmatter is the block between the first `---` and the
 * next `---` at the start of the file.
 */
function stripFrontmatter(md: string): string {
  if (!md.startsWith("---")) return md;
  const end = md.indexOf("---", 3);
  if (end === -1) return md;
  return md.slice(end + 3).trimStart();
}

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

export function ManualViewer({ manualId, onBack }: ManualViewerProps) {
  const { t } = useTranslation();
  const [manual, setManual] = useState<ManualDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getManual(manualId)
      .then((data) => {
        if (!cancelled) setManual(data);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [manualId]);

  // Compute the base URL for rewriting relative image paths.
  // md_file_path is like "MWS-150-A/manual.md", so the base
  // directory is "MWS-150-A/".
  const imageBaseUrl = useMemo(() => {
    if (!manual?.md_file_path) return "/manuals/data/";
    const dir = manual.md_file_path.replace(/[^/]*$/, "");
    return `/manuals/data/${dir}`;
  }, [manual?.md_file_path]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="h-6 w-6 animate-spin mr-2 text-muted-foreground" />
        <span className="text-sm text-muted-foreground">Loading manual...</span>
      </div>
    );
  }

  if (error || !manual) {
    return (
      <div className="space-y-4">
        <Button variant="ghost" size="sm" onClick={onBack}>
          <ArrowLeft className="h-4 w-4 mr-1" />
          {t("manuals.backToList")}
        </Button>
        <Alert variant="destructive">
          <AlertDescription>{error || "Manual not found"}</AlertDescription>
        </Alert>
      </div>
    );
  }

  const body = manual.content ? stripFrontmatter(manual.content) : null;

  return (
    <div className="space-y-4">
      <Button variant="ghost" size="sm" onClick={onBack}>
        <ArrowLeft className="h-4 w-4 mr-1" />
        {t("manuals.backToList")}
      </Button>

      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <BookOpen className="h-5 w-5 text-muted-foreground" />
            <CardTitle className="text-lg">{manual.filename}</CardTitle>
          </div>
          {/* Metadata banner */}
          <div className="flex flex-wrap gap-2 mt-2">
            {manual.vehicle_model && (
              <Badge variant="outline">{manual.vehicle_model}</Badge>
            )}
            {manual.page_count && (
              <Badge variant="secondary">
                {manual.page_count} {t("manuals.pages")}
              </Badge>
            )}
            {manual.section_count && (
              <Badge variant="secondary">
                {manual.section_count} {t("manuals.sections")}
              </Badge>
            )}
            {manual.chunk_count && (
              <Badge variant="secondary">
                {manual.chunk_count} {t("manuals.chunks")}
              </Badge>
            )}
            {manual.language && (
              <Badge variant="secondary">{manual.language}</Badge>
            )}
            {manual.converter && (
              <Badge variant="outline">{manual.converter}</Badge>
            )}
          </div>
        </CardHeader>
        <CardContent>
          {body ? (
            <article className="prose prose-sm dark:prose-invert max-w-none">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  // Rewrite relative image paths to serve
                  // from the nginx /manuals/data/ endpoint.
                  img: ({ node, src, alt, ...props }) => {
                    let resolvedSrc = src || "";
                    if (
                      resolvedSrc &&
                      !resolvedSrc.startsWith("http") &&
                      !resolvedSrc.startsWith("/")
                    ) {
                      resolvedSrc = imageBaseUrl + resolvedSrc;
                    }
                    return (
                      <img
                        src={resolvedSrc}
                        alt={alt || ""}
                        loading="lazy"
                        className="max-w-full h-auto rounded"
                        {...props}
                      />
                    );
                  },
                  // Open external links in new tab.
                  a: ({ node, href, children, ...props }) => (
                    <a
                      href={href}
                      target={href?.startsWith("http") ? "_blank" : undefined}
                      rel={href?.startsWith("http") ? "noopener noreferrer" : undefined}
                      {...props}
                    >
                      {children}
                    </a>
                  ),
                }}
              >
                {body}
              </ReactMarkdown>
            </article>
          ) : (
            <p className="text-sm text-muted-foreground">
              No content available.
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
