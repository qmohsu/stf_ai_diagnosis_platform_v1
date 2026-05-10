"use client";

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, Save } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { AudioRecorder } from "@/components/AudioRecorder";
import { StarRating } from "@/components/goldens/StarRating";
import {
  fetchGoldenReviewAudioBlob,
  submitGoldenReview,
  uploadGoldenReviewAudio,
} from "@/lib/api";
import type {
  GoldenReviewOut,
  GoldenReviewStatus,
} from "@/lib/types";

interface ReviewSubmitFormProps {
  entryId: string;
  /** Existing review (null if reviewer hasn't graded yet). */
  initial: GoldenReviewOut | null;
  /** Called after successful submit; parent updates the entry's
   * cached review via this. */
  onSubmitted: (updated: GoldenReviewOut) => void;
}

/**
 * Composite form: 4 star ratings (overall + 3 per-dimension),
 * status radio, free-text notes, audio recorder.  Submits to
 * ``/v2/goldens/{id}/review`` and reports the updated review
 * back to the parent for cache refresh.
 */
export function ReviewSubmitForm({
  entryId,
  initial,
  onSubmitted,
}: ReviewSubmitFormProps) {
  const { t } = useTranslation();
  const [overallStar, setOverallStar] = useState<number | null>(
    initial?.star_rating ?? null,
  );
  const [questionStar, setQuestionStar] = useState<number | null>(
    initial?.question_realism_score ?? null,
  );
  const [answerStar, setAnswerStar] = useState<number | null>(
    initial?.answer_correctness_score ?? null,
  );
  const [citationStar, setCitationStar] = useState<number | null>(
    initial?.citation_faithfulness_score ?? null,
  );
  const [status, setStatus] = useState<GoldenReviewStatus>(
    initial?.status ?? "draft",
  );
  const [notes, setNotes] = useState(initial?.notes ?? "");

  const [pendingAudio, setPendingAudio] = useState<{
    blob: Blob;
    durationSeconds: number;
  } | null>(null);
  const [existingAudioUrl, setExistingAudioUrl] = useState<
    string | null
  >(null);

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<string | null>(null);

  // Lazy-fetch the existing audio (auth-gated → must use blob URL).
  useEffect(() => {
    let cancelled = false;
    let objectUrl: string | null = null;
    if (initial?.has_audio) {
      fetchGoldenReviewAudioBlob(entryId)
        .then((blob) => {
          if (cancelled) return;
          objectUrl = URL.createObjectURL(blob);
          setExistingAudioUrl(objectUrl);
        })
        .catch(() => {
          if (cancelled) return;
          // Silent — audio playback is non-essential.
        });
    }
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [entryId, initial?.has_audio]);

  async function handleSubmit() {
    setSubmitting(true);
    setError(null);
    try {
      let audioToken: string | null = null;
      let audioDuration: number | null = null;
      if (pendingAudio) {
        const upload = await uploadGoldenReviewAudio(
          pendingAudio.blob,
        );
        audioToken = upload.audio_token;
        audioDuration = pendingAudio.durationSeconds;
      }
      const updated = await submitGoldenReview(entryId, {
        star_rating: overallStar,
        question_realism_score: questionStar,
        answer_correctness_score: answerStar,
        citation_faithfulness_score: citationStar,
        status,
        notes: notes.trim() ? notes : null,
        audio_token: audioToken,
        audio_duration_seconds: audioDuration,
      });
      onSubmitted(updated);
      setPendingAudio(null);
      setSavedAt(new Date().toISOString());
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2">
        <StarRating
          value={overallStar}
          onChange={setOverallStar}
          label={t("goldens.review.overallRating")}
          description={t("goldens.review.overallDesc")}
          size={32}
        />
        <StarRating
          value={questionStar}
          onChange={setQuestionStar}
          label={t("goldens.review.questionRealism")}
          description={t("goldens.review.questionRealismDesc")}
        />
        <StarRating
          value={answerStar}
          onChange={setAnswerStar}
          label={t("goldens.review.answerCorrectness")}
          description={t("goldens.review.answerCorrectnessDesc")}
        />
        <StarRating
          value={citationStar}
          onChange={setCitationStar}
          label={t("goldens.review.citationFaithfulness")}
          description={t("goldens.review.citationFaithfulnessDesc")}
        />
      </div>

      <div className="space-y-1">
        <div className="text-sm font-medium">{t("goldens.review.status")}</div>
        <div className="flex flex-wrap gap-2">
          {(
            [
              { v: "draft", label: t("goldens.review.statusDraft") },
              { v: "accept", label: t("goldens.review.statusAccept") },
              {
                v: "needs_revision",
                label: t("goldens.review.statusNeedsRevision"),
              },
              { v: "reject", label: t("goldens.review.statusReject") },
            ] as { v: GoldenReviewStatus; label: string }[]
          ).map((opt) => {
            const checked = status === opt.v;
            return (
              <label
                key={opt.v}
                className={`cursor-pointer select-none rounded-md border px-3 py-1.5 text-sm ${
                  checked
                    ? "border-primary bg-primary/10 font-medium"
                    : "border-border hover:bg-muted/40"
                }`}
              >
                <input
                  type="radio"
                  name="status"
                  value={opt.v}
                  checked={checked}
                  onChange={() => setStatus(opt.v)}
                  className="hidden"
                />
                {opt.label}
              </label>
            );
          })}
        </div>
      </div>

      <div className="space-y-1">
        <div className="text-sm font-medium">
          {t("goldens.review.notes")}
        </div>
        <Textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder={t("goldens.review.notesPlaceholder")}
          rows={5}
          className="resize-y"
        />
      </div>

      <div className="space-y-1">
        <div className="text-sm font-medium">
          {t("goldens.review.audioFeedback")}
        </div>
        {existingAudioUrl && !pendingAudio && (
          <div className="mb-2 rounded border border-border bg-muted/30 p-2">
            <div className="mb-1 text-xs text-muted-foreground">
              {t("goldens.review.existingRecording")}
            </div>
            <audio src={existingAudioUrl} controls className="w-full" />
          </div>
        )}
        <AudioRecorder
          onRecordingComplete={(blob, durationSeconds) =>
            setPendingAudio({ blob, durationSeconds })
          }
          onRecordingCleared={() => setPendingAudio(null)}
        />
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <div className="flex items-center gap-3">
        <Button
          onClick={handleSubmit}
          disabled={submitting}
          className="gap-2"
        >
          {submitting ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Save className="h-4 w-4" />
          )}
          {initial
            ? t("goldens.review.submitUpdate")
            : t("goldens.review.submitNew")}
        </Button>
        {savedAt && (
          <span className="text-xs text-muted-foreground">
            Saved {new Date(savedAt).toLocaleTimeString()}
          </span>
        )}
      </div>
    </div>
  );
}
