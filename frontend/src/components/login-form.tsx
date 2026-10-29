"use client";

import { useState } from "react";
import { useSearchParams } from "next/navigation";
import { AlertTriangle, Eye, EyeOff, LoaderCircle, LockKeyhole, UserRound } from "lucide-react";
import { useLocale } from "@/components/locale-provider";
import { clearPortalCaches } from "@/lib/client-session";

type LoginResponse = { user?: { username: string }; error?: { code?: string } };

export function LoginForm() {
  const { messages } = useLocale();
  const searchParams = useSearchParams();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(searchParams.get("expired") === "1" ? messages.login.expired : null);

  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!username || !password || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        credentials: "same-origin",
        cache: "no-store",
        body: JSON.stringify({ username, password }),
      });
      let body: LoginResponse = {};
      try { body = await response.json() as LoginResponse; } catch { /* Use the status fallback below. */ }
      if (!response.ok || !body.user) {
        if (response.status === 401) setError(messages.login.invalid);
        else if (response.status === 503) setError(messages.login.unavailable);
        else if (response.status === 429) setError(messages.login.rateLimited);
        else setError(messages.login.failed);
        setPassword("");
        return;
      }
      await clearPortalCaches();
      window.location.assign("/");
    } catch {
      setError(messages.login.unavailable);
      setPassword("");
    } finally { setSubmitting(false); }
  };

  return (
    <main className="login-page">
      <form className="login-card" onSubmit={submit} noValidate>
        <h1>{messages.login.title}</h1>
        <div className="login-field">
          <label htmlFor="login-username">{messages.login.username}</label>
          <div className="login-control">
            <UserRound aria-hidden="true" />
            <input
              id="login-username"
              name="username"
              type="text"
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
              maxLength={50}
              placeholder={messages.login.usernamePlaceholder}
              value={username}
              onChange={(event) => { setUsername(event.target.value); setError(null); }}
              required
              autoFocus
            />
          </div>
        </div>
        <div className="login-field">
          <label htmlFor="login-password">{messages.login.password}</label>
          <div className="login-control">
            <LockKeyhole aria-hidden="true" />
            <input
              id="login-password"
              name="password"
              type={showPassword ? "text" : "password"}
              autoComplete="current-password"
              maxLength={256}
              placeholder={messages.login.passwordPlaceholder}
              value={password}
              onChange={(event) => { setPassword(event.target.value); setError(null); }}
              required
            />
            <button
              type="button"
              className="password-toggle"
              aria-label={showPassword ? messages.login.hidePassword : messages.login.showPassword}
              aria-pressed={showPassword}
              onClick={() => setShowPassword((visible) => !visible)}
            >
              {showPassword ? <EyeOff aria-hidden="true" /> : <Eye aria-hidden="true" />}
              <span>{showPassword ? messages.login.hide : messages.login.show}</span>
            </button>
          </div>
        </div>
        {error && <p className="login-error" role="alert"><AlertTriangle aria-hidden="true" />{error}</p>}
        <button className="login-submit" type="submit" disabled={!username || !password || submitting}>
          {submitting && <LoaderCircle className="spin" aria-hidden="true" />}
          {submitting ? messages.login.submitting : messages.login.submit}
        </button>
        <p className="login-duration">{messages.login.duration}</p>
      </form>
      <p className="login-authorized">{messages.login.authorizedOnly}</p>
    </main>
  );
}
