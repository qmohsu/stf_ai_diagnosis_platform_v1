"use client";

import { useEffect, useRef, useState } from "react";
import { redirectToLogin } from "@/lib/client-session";

const MAX_FILE = 50 * 1024 * 1024;

export function LogUpload({ vehicleId, onStored }: { vehicleId: string; onStored: () => void }) {
  const xhr = useRef<XMLHttpRequest | null>(null);
  const fileInput = useRef<HTMLInputElement | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [progress, setProgress] = useState(0);
  const [phase, setPhase] = useState<"idle" | "uploading" | "processing">("idle");
  const [message, setMessage] = useState("");
  useEffect(() => () => xhr.current?.abort(), []);

  function upload(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file || phase !== "idle") return;
    if (file.size > MAX_FILE) { setMessage("檔案超過 50 MB 上限"); return; }
    const request = new XMLHttpRequest();
    xhr.current = request; setProgress(0); setMessage(""); setPhase("uploading");
    request.open("POST", `/api/portal/vehicles/${vehicleId}/logs`);
    request.withCredentials = true;
    request.responseType = "json";
    request.setRequestHeader("Accept", "application/json");
    request.upload.onprogress = (e) => {
      if (e.lengthComputable) {
        const pct = Math.min(100, Math.round(e.loaded / e.total * 100));
        setProgress(pct);
        if (pct === 100) setPhase("processing");
      }
    };
    request.onload = () => {
      xhr.current = null; setPhase("idle");
      const payload = request.response as { duplicate?: boolean; error?: { code?: string; message?: string } } | null;
      if (request.status >= 200 && request.status < 300) {
        setMessage(payload?.duplicate ? "此日誌已存在，沒有重複保存。" : "日誌已保存。現在可從列表選擇並診斷。");
        setFile(null); if (fileInput.current) fileInput.current.value = ""; onStored(); return;
      }
      if (request.status === 401) { void redirectToLogin(); return; }
      setMessage(uploadError(request.status, payload?.error?.code));
    };
    request.onerror = () => { xhr.current = null; setPhase("idle"); setMessage("網絡中斷，請檢查連線後手動重試。"); };
    request.onabort = () => { xhr.current = null; setPhase("idle"); setMessage("上傳已取消。"); };
    const body = new FormData(); body.set("file", file);
    request.send(body);
  }
  return <form className="portal-form" onSubmit={upload}>
    <label>選擇行車日誌<input ref={fileInput} type="file" accept=".tsv,.csv,text/tab-separated-values,text/csv" onChange={(e) => { setFile(e.target.files?.[0] ?? null); setMessage(""); }} /></label>
    {file && <p className="inline-state">{file.name} · {(file.size / 1024 / 1024).toFixed(1)} MB</p>}
    {phase !== "idle" && <div role="status"><progress max={100} value={progress}>{progress}%</progress><p>{phase === "processing" ? "檔案已送達前端，伺服器正在處理…" : `上傳中 ${progress}%`}</p></div>}
    {message && <p role="status" className="inline-state">{message}</p>}
    <div className="form-actions"><button className="button" type="submit" disabled={!file || phase !== "idle"}>{message.startsWith("網絡中斷") ? "重新上傳" : "上傳日誌"}</button>
      {phase !== "idle" && <button className="button secondary" type="button" onClick={() => xhr.current?.abort()}>取消上傳</button>}</div>
  </form>;
}

function uploadError(status: number, code?: string): string {
  if (status === 413 || code === "FILE_TOO_LARGE") return "檔案超過 50 MB 上限。";
  if (code === "VIN_MISMATCH") return "日誌中的 VIN 與這台車不符，檔案沒有保存。";
  if (code === "UNSUPPORTED_FORMAT") return "不支援此日誌格式，請使用支援的 TSV 或 CSV。";
  if (status === 404) return "找不到此車輛，請返回車輛列表。";
  if (status === 429) return "操作太頻繁，請稍後重試。";
  if (status === 503) return "V3 服務暫時無法使用，請稍後手動重試。";
  return "上傳失敗，請檢查檔案後重試。";
}
