"use client";

import Link from "next/link";
import { useState } from "react";

export function RegisterForm() {
  const [form, setForm] = useState({ username: "", password: "", invite_code: "", email: "", display_name: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      const response = await fetch("/api/auth/register", {
        method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" },
        credentials: "same-origin", cache: "no-store",
        body: JSON.stringify({ ...form, email: form.email || null, display_name: form.display_name || null }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null) as { error?: { message?: string } } | null;
        setError(body?.error?.message || (response.status === 429 ? "嘗試次數過多，請稍後再試" : "註冊失敗，請重試"));
        return;
      }
      setForm({ username: "", password: "", invite_code: "", email: "", display_name: "" });
      setDone(true);
    } catch { setError("服務暫時無法使用，請稍後再試"); }
    finally { setBusy(false); }
  }
  return <main className="login-page"><form className="login-card" onSubmit={submit}>
    <h1>加入 STF V3 工作坊</h1>
    {done ? <p role="status">註冊成功。<Link href="/login">前往登入</Link></p> : <>
      <p>使用工作坊提供的邀請碼註冊。</p>
      {([
        ["username", "用戶名稱", "text", "username", true],
        ["password", "密碼（至少 10 字元）", "password", "new-password", true],
        ["invite_code", "邀請碼", "text", "off", true],
        ["email", "電子郵件（選填）", "email", "email", false],
        ["display_name", "顯示名稱（選填）", "text", "name", false],
      ] as const).map(([key, label, type, autocomplete, required]) => <label className="login-field" key={key}>{label}
        <input className="form-input" type={type} autoComplete={autocomplete} required={required}
          maxLength={key === "password" ? 256 : key === "username" ? 50 : key === "invite_code" ? 32 : key === "email" ? 254 : 100}
          minLength={key === "password" ? 10 : undefined}
          value={form[key]} onChange={(event) => setForm({ ...form, [key]: event.target.value })} />
      </label>)}
      {error && <p className="login-error" role="alert">{error}</p>}
      <button className="login-submit" disabled={busy} type="submit">{busy ? "註冊中…" : "註冊"}</button>
      <p><Link href="/login">已有帳號？返回登入</Link></p>
    </>}
  </form></main>;
}
