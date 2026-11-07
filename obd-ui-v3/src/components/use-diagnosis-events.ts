"use client";

import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { PortalApiError, portalEventStream, portalFetch } from "@/lib/api-client";
import { readDiagnosisStream } from "@/lib/diagnosis-events";
import type { DiagnosisConversation, DiagnosisEvent } from "@/lib/v3-domain";

export type ProcessState = "loading" | "live" | "reconnecting" | "complete" | "archived" | "failed";

export function useDiagnosisEvents(conversation: DiagnosisConversation | undefined) {
  const [events, setEvents] = useState<DiagnosisEvent[]>([]);
  const [state, setState] = useState<ProcessState>("loading");
  const [failure, setFailure] = useState("");
  const [restart, setRestart] = useState(0);
  const queryClient = useQueryClient();
  const conversationId = conversation?.id;
  const archived = conversation?.events_archived;
  const initialStatus = conversation?.status;

  useEffect(() => {
    if (!conversationId) return;
    const controller = new AbortController();
    let lastSeq = 0;
    let sawDone = false;
    let delayMs = 1_000;
    setEvents([]);
    setFailure("");
    setState(archived ? "archived" : "loading");
    if (archived) return () => controller.abort();

    const addEvent = (event: DiagnosisEvent) => {
      if (!Number.isSafeInteger(event.seq) || event.seq <= lastSeq) return;
      lastSeq = event.seq;
      if (event.event_type === "done") sawDone = true;
      setEvents((current) => [...current, event]);
    };
    const refreshConversation = async () => {
      const response = await portalFetch<{ conversation: DiagnosisConversation }>(`/api/portal/conversations/${conversationId}`, { signal: controller.signal });
      if (response.conversation.status === "done" || response.conversation.status === "error" || response.conversation.status === "cancelled") {
        void queryClient.invalidateQueries({ queryKey: ["conversation", conversationId] });
        return true;
      }
      return false;
    };
    const run = async () => {
      while (!controller.signal.aborted) {
        try {
          const replay = await portalFetch<{ events: DiagnosisEvent[] }>(`/api/portal/conversations/${conversationId}/events?after_seq=${lastSeq}`, { signal: controller.signal });
          if (!Array.isArray(replay.events)) throw new Error("診斷過程的回放格式不正確");
          replay.events.forEach(addEvent);
          if (sawDone || initialStatus === "done" || initialStatus === "error" || initialStatus === "cancelled") {
            await queryClient.invalidateQueries({ queryKey: ["conversation", conversationId] });
            setState("complete");
            return;
          }
          setState("live");
          const response = await portalEventStream(`/api/portal/conversations/${conversationId}/events?after_seq=${lastSeq}`, controller.signal);
          const complete = await readDiagnosisStream(response, addEvent);
          if (complete || sawDone || await refreshConversation()) {
            await queryClient.invalidateQueries({ queryKey: ["conversation", conversationId] });
            setState("complete");
            return;
          }
        } catch (error) {
          if (controller.signal.aborted) return;
          if (error instanceof PortalApiError && error.status === 410) {
            setState("archived");
            void queryClient.invalidateQueries({ queryKey: ["conversation", conversationId] });
            return;
          }
          if (error instanceof PortalApiError && [401, 403, 404].includes(error.status)) {
            setFailure(error.status === 401 ? "登入狀態已失效" : error.status === 403 ? "無權查看此診斷過程" : "找不到診斷過程");
            setState("failed");
            return;
          }
          setFailure(error instanceof Error ? error.message : "連線暫時中斷");
        }
        setState("reconnecting");
        await sleepUntilRetry(delayMs, controller.signal);
        delayMs = Math.min(delayMs * 2, 15_000);
      }
    };
    void run();
    return () => controller.abort();
  }, [archived, conversationId, initialStatus, queryClient, restart]);

  return { events, state, failure, retry: () => setRestart((current) => current + 1) };
}

function sleepUntilRetry(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const finish = () => { clearTimeout(timeout); signal.removeEventListener("abort", finish); resolve(); };
    const timeout = setTimeout(finish, ms);
    signal.addEventListener("abort", finish, { once: true });
  });
}
