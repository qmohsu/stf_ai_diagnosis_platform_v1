"use client";

import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Star } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { submitFeedback } from "@/lib/api";
import { cn } from "@/lib/utils";

interface FeedbackFormProps {
  sessionId: string;
  feedbackTab: "summary" | "detailed" | "rag" | "ai_diagnosis" | "premium_diagnosis";
}

export function FeedbackForm({ sessionId, feedbackTab }: FeedbackFormProps) {
  const { t } = useTranslation();
  const [rating, setRating] = useState(0);
  const [hoverRating, setHoverRating] = useState(0);
  const [isHelpful, setIsHelpful] = useState<boolean | null>(null);
  const [comments, setComments] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async () => {
    if (rating === 0 || isHelpful === null) return;
    setLoading(true);
    setError(null);
    try {
      await submitFeedback(sessionId, {
        rating,
        is_helpful: isHelpful,
        comments: comments || undefined,
      }, feedbackTab);
      setSubmitted(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("feedbackForm.submitFailed"));
    } finally {
      setLoading(false);
    }
  };

  const handleReset = () => {
    setRating(0);
    setIsHelpful(null);
    setComments("");
    setSubmitted(false);
    setError(null);
  };

  if (submitted) {
    return (
      <Card>
        <CardContent className="p-6 space-y-4">
          <Alert>
            <AlertDescription>
              {t("feedbackForm.submitted")}
            </AlertDescription>
          </Alert>
          <Button variant="outline" className="w-full" onClick={handleReset}>
            {t("feedbackForm.submitAnother")}
          </Button>
        </CardContent>
      </Card>
    );
  }

  const viewName = t(`feedbackForm.view.${feedbackTab}`);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-lg">
          {t("feedbackForm.title", { view: viewName })}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Star Rating */}
        <div className="space-y-2">
          <label htmlFor={`rating-${feedbackTab}`} className="text-sm font-medium">{t("feedbackForm.rating")}</label>
          <div className="flex gap-1">
            {[1, 2, 3, 4, 5].map((star) => (
              <button
                key={star}
                type="button"
                aria-label={t("feedbackForm.rateOutOf5", { n: star })}
                onMouseEnter={() => setHoverRating(star)}
                onMouseLeave={() => setHoverRating(0)}
                onClick={() => setRating(star)}
                className="p-0.5"
              >
                <Star
                  className={cn(
                    "h-6 w-6 transition-colors",
                    (hoverRating || rating) >= star
                      ? "fill-amber-400 text-amber-400"
                      : "text-gray-300",
                  )}
                />
              </button>
            ))}
          </div>
        </div>

        {/* Helpful Toggle */}
        <div className="space-y-2">
          <label htmlFor={`helpful-${feedbackTab}`} className="text-sm font-medium">{t("feedbackForm.wasHelpful")}</label>
          <div className="flex gap-2">
            <Button
              variant={isHelpful === true ? "default" : "outline"}
              size="sm"
              onClick={() => setIsHelpful(true)}
            >
              {t("feedbackForm.yes")}
            </Button>
            <Button
              variant={isHelpful === false ? "default" : "outline"}
              size="sm"
              onClick={() => setIsHelpful(false)}
            >
              {t("feedbackForm.no")}
            </Button>
          </div>
        </div>

        {/* Comments */}
        <div className="space-y-2">
          <label htmlFor={`comments-${feedbackTab}`} className="text-sm font-medium">{t("feedbackForm.commentsOptional")}</label>
          <Textarea
            id={`comments-${feedbackTab}`}
            placeholder={t("feedbackForm.commentsPlaceholder")}
            value={comments}
            onChange={(e) => setComments(e.target.value)}
            className="min-h-[80px]"
          />
        </div>

        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        <Button
          onClick={handleSubmit}
          disabled={rating === 0 || isHelpful === null || loading}
          className="w-full"
        >
          {loading ? t("feedbackForm.submitting") : t("feedbackForm.submit")}
        </Button>
      </CardContent>
    </Card>
  );
}
