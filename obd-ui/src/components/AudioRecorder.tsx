"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Mic, Square, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface AudioRecorderProps {
  onRecordingComplete: (blob: Blob, durationSeconds: number) => void;
  onRecordingCleared: () => void;
  maxDurationSeconds?: number;
  disabled?: boolean;
}

type RecorderState = "idle" | "recording" | "recorded";

export function AudioRecorder({
  onRecordingComplete,
  onRecordingCleared,
  maxDurationSeconds = 120,
  disabled = false,
}: AudioRecorderProps) {
  const { t } = useTranslation();
  const [state, setState] = useState<RecorderState>("idle");
  const [elapsed, setElapsed] = useState(0);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [permError, setPermError] = useState(false);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const startTimeRef = useRef(0);
  const audioUrlRef = useRef<string | null>(null);

  // Cleanup on unmount.
  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
      if (streamRef.current) {
        streamRef.current
          .getTracks()
          .forEach((track) => track.stop());
      }
      if (audioUrlRef.current) {
        URL.revokeObjectURL(audioUrlRef.current);
      }
    };
  }, []);

  const stopRecording = useCallback(() => {
    if (mediaRecorderRef.current?.state === "recording") {
      mediaRecorderRef.current.stop();
    }
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    if (streamRef.current) {
      streamRef.current
        .getTracks()
        .forEach((track) => track.stop());
      streamRef.current = null;
    }
  }, []);

  const startRecording = useCallback(async () => {
    setPermError(false);
    chunksRef.current = [];

    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: true,
      });
    } catch {
      setPermError(true);
      return;
    }
    streamRef.current = stream;

    // Pick a supported MIME type.
    const mimeType = MediaRecorder.isTypeSupported(
      "audio/webm;codecs=opus",
    )
      ? "audio/webm;codecs=opus"
      : "audio/webm";

    const recorder = new MediaRecorder(stream, { mimeType });
    mediaRecorderRef.current = recorder;

    recorder.ondataavailable = (e) => {
      if (e.data.size > 0) chunksRef.current.push(e.data);
    };

    recorder.onstop = () => {
      const blob = new Blob(chunksRef.current, {
        type: mimeType,
      });
      const duration = Math.round(
        (Date.now() - startTimeRef.current) / 1000,
      );
      const url = URL.createObjectURL(blob);
      audioUrlRef.current = url;
      setAudioUrl(url);
      setState("recorded");
      setElapsed(duration);
      onRecordingComplete(blob, duration);
    };

    startTimeRef.current = Date.now();
    recorder.start(1000); // collect chunks every 1s
    setState("recording");
    setElapsed(0);

    // Elapsed timer.
    timerRef.current = setInterval(() => {
      const secs = Math.round(
        (Date.now() - startTimeRef.current) / 1000,
      );
      setElapsed(secs);
      if (secs >= maxDurationSeconds) {
        stopRecording();
      }
    }, 500);
  }, [maxDurationSeconds, onRecordingComplete, stopRecording]);

  const handleDelete = useCallback(() => {
    if (audioUrl) {
      URL.revokeObjectURL(audioUrl);
      audioUrlRef.current = null;
      setAudioUrl(null);
    }
    setState("idle");
    setElapsed(0);
    onRecordingCleared();
  }, [audioUrl, onRecordingCleared]);

  const formatTime = (s: number) => {
    const mm = String(Math.floor(s / 60)).padStart(2, "0");
    const ss = String(s % 60).padStart(2, "0");
    return `${mm}:${ss}`;
  };

  return (
    <div className="space-y-2">
      <label className="text-sm font-medium">
        {t("feedbackForm.audioRecording")}
      </label>

      {state === "idle" && (
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={startRecording}
            disabled={disabled}
          >
            <Mic className="h-4 w-4 mr-1" />
            {t("feedbackForm.startRecording")}
          </Button>
          <span className="text-xs text-muted-foreground">
            {t("feedbackForm.maxDuration", {
              seconds: maxDurationSeconds,
            })}
          </span>
        </div>
      )}

      {state === "recording" && (
        <div className="flex items-center gap-3">
          <Button
            type="button"
            variant="destructive"
            size="sm"
            onClick={stopRecording}
          >
            <Square className="h-4 w-4 mr-1" />
            {t("feedbackForm.stopRecording")}
          </Button>
          <span
            className={cn(
              "text-sm font-mono tabular-nums",
              "text-red-500 animate-pulse",
            )}
          >
            {formatTime(elapsed)} / {formatTime(maxDurationSeconds)}
          </span>
        </div>
      )}

      {state === "recorded" && audioUrl && (
        <div className="flex items-center gap-2">
          {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
          <audio controls preload="metadata" src={audioUrl} className="h-8" />
          <span className="text-xs text-muted-foreground font-mono tabular-nums">
            {formatTime(elapsed)}
          </span>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={handleDelete}
            disabled={disabled}
          >
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      )}

      {permError && (
        <p className="text-xs text-red-500">
          {t("feedbackForm.micPermissionDenied")}
        </p>
      )}
    </div>
  );
}
