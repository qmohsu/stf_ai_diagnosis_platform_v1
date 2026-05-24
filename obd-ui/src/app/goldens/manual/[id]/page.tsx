"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { useTranslation } from "react-i18next";
import { ArrowLeft, BookOpen, Languages, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { QuestionCard } from "@/components/goldens/QuestionCard";
import { ReviewSubmitForm } from "@/components/goldens/ReviewSubmitForm";
import { TeamFeedbackList } from "@/components/goldens/TeamFeedbackList";
import { getGolden } from "@/lib/api";
import type { GoldenEntryDetail } from "@/lib/types";

export default function GoldenDetailPage() {
  const { t } = useTranslation();
  const params = useParams<{ id: string }>();

  const [entry, setEntry] = useState<GoldenEntryDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [language, setLanguage] = useState<"en" | "zh">("zh");
  // Incremented after the user submits their own review so the
  // <TeamFeedbackList> refetches and shows the just-added entry.
  const [feedbackRefreshKey, setFeedbackRefreshKey] = useState(0);

  // Derive entryId reactively — guards against `params` being
  // briefly undefined during SSR/first-client pass, which would
  // otherwise trip React #418 hydration mismatch when the
  // decoded ID differs between passes.
  const entryId =
    typeof params?.id === "string"
      ? decodeURIComponent(params.id)
      : "";

  useEffect(() => {
    if (!entryId) return;
    setLoading(true);
    setError(null);
    getGolden(entryId)
      .then(setEntry)
      .catch((err) =>
        setError(err instanceof Error ? err.message : String(err)),
      )
      .finally(() => setLoading(false));
  }, [entryId]);

  // Default to English if Chinese not available.
  useEffect(() => {
    if (entry && !entry.question_zh && language === "zh") {
      setLanguage("en");
    }
  }, [entry, language]);

  function onReviewSubmitted() {
    // Reviews are append-only — bump the refresh key so the
    // team-feedback panel re-fetches and the new row appears.
    setFeedbackRefreshKey((k) => k + 1);
  }

  if (loading) {
    return (
      <div className="container mx-auto px-4 py-6">
        <div className="flex items-center gap-2 text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          {t("goldens.detail.loadingEntry")}
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="container mx-auto px-4 py-6 space-y-3">
        <Link
          href="/goldens/manual"
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" />
          {t("goldens.detail.backToListing")}
        </Link>
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      </div>
    );
  }

  if (!entry) return null;

  return (
    <div className="container mx-auto px-4 py-6 space-y-4">
      <div className="flex items-center justify-between">
        <Link
          href="/goldens/manual"
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" />
          {t("goldens.detail.backToListing")}
        </Link>

        <div className="flex items-center gap-2">
          {entry.requires_image || entry.golden_citations.length > 0 ? (
            <Link
              href={`/manuals/${entry.manual_id}`}
              className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
              target="_blank"
              rel="noreferrer"
            >
              <BookOpen className="h-4 w-4" />
              {t("goldens.detail.openManual")}
            </Link>
          ) : null}

          <Button
            variant="outline"
            size="sm"
            onClick={() =>
              setLanguage(language === "en" ? "zh" : "en")
            }
            disabled={!entry.question_zh}
            className="gap-2"
          >
            <Languages className="h-4 w-4" />
            {language === "en"
              ? t("goldens.detail.switchToZh")
              : t("goldens.detail.switchToEn")}
          </Button>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>{t("goldens.detail.questionCard")}</CardTitle>
        </CardHeader>
        <CardContent>
          <QuestionCard entry={entry} language={language} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{t("goldens.detail.yourReview")}</CardTitle>
        </CardHeader>
        <CardContent>
          <ReviewSubmitForm
            entryId={entry.id}
            onSubmitted={onReviewSubmitted}
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{t("goldens.detail.teamHistory")}</CardTitle>
        </CardHeader>
        <CardContent>
          <TeamFeedbackList
            entryId={entry.id}
            refreshKey={feedbackRefreshKey}
          />
        </CardContent>
      </Card>
    </div>
  );
}
