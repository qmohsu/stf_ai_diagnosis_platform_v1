"use client";

import { useState } from "react";
import { Star } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { submitFeedback } from "@/lib/api";
import { cn } from "@/lib/utils";

interface FeedbackFormProps {
  sessionId: string;
}

export function FeedbackForm({ sessionId }: FeedbackFormProps) {
  const [rating, setRating] = useState(0);
  const [hoverRating, setHoverRating] = useState(0);
  const [isHelpful, setIsHelpful] = useState<boolean | null>(null);
  const [comments, setComments] = useState("");
  const [correctedDiagnosis, setCorrectedDiagnosis] = useState("");
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
        corrected_diagnosis: correctedDiagnosis || undefined,
      });
      setSubmitted(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to submit feedback");
    } finally {
      setLoading(false);
    }
  };

  if (submitted) {
    return (
      <Card>
        <CardContent className="p-6">
          <Alert>
            <AlertDescription>
              Feedback submitted successfully. Thank you!
            </AlertDescription>
          </Alert>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-lg">Expert Feedback</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Star Rating */}
        <div className="space-y-2">
          <label className="text-sm font-medium">Rating</label>
          <div className="flex gap-1">
            {[1, 2, 3, 4, 5].map((star) => (
              <button
                key={star}
                type="button"
                aria-label={`Rate ${star} out of 5`}
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
          <label className="text-sm font-medium">Was this analysis helpful?</label>
          <div className="flex gap-2">
            <Button
              variant={isHelpful === true ? "default" : "outline"}
              size="sm"
              onClick={() => setIsHelpful(true)}
            >
              Yes
            </Button>
            <Button
              variant={isHelpful === false ? "default" : "outline"}
              size="sm"
              onClick={() => setIsHelpful(false)}
            >
              No
            </Button>
          </div>
        </div>

        {/* Comments */}
        <div className="space-y-2">
          <label className="text-sm font-medium">Comments (optional)</label>
          <Textarea
            placeholder="Any additional comments..."
            value={comments}
            onChange={(e) => setComments(e.target.value)}
            className="min-h-[80px]"
          />
        </div>

        {/* Corrected Diagnosis (shown when not helpful) */}
        {isHelpful === false && (
          <div className="space-y-2">
            <label className="text-sm font-medium">Corrected Diagnosis</label>
            <Textarea
              placeholder="What would be the correct diagnosis?"
              value={correctedDiagnosis}
              onChange={(e) => setCorrectedDiagnosis(e.target.value)}
              className="min-h-[80px]"
            />
          </div>
        )}

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
          {loading ? "Submitting..." : "Submit Feedback"}
        </Button>
      </CardContent>
    </Card>
  );
}
