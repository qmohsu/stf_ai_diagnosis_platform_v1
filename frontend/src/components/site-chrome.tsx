"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { LogOut } from "lucide-react";
import { clearPortalCaches } from "@/lib/client-session";
import type { PortalUser } from "@/server/auth-session";

export function SiteChrome({ children, user }: { children: React.ReactNode; user: PortalUser | null }) {
  const [hydrated, setHydrated] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);
  const [logoutError, setLogoutError] = useState(false);
  useEffect(() => setHydrated(true), []);
  async function logout() {
    if (loggingOut) return;
    setLoggingOut(true);
    setLogoutError(false);
    try {
      const response = await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin", cache: "no-store" });
      if (!response.ok) throw new Error("Logout failed");
      await clearPortalCaches();
      window.location.assign("/login");
    } catch {
      setLogoutError(true);
      setLoggingOut(false);
    }
  }
  return <>
    <header className="topbar"><Link className="brand-link" href="/" aria-label="STF V3 首頁"><span className="brand-mark">S3</span><span><strong>STF V3</strong><small>車輛診斷平台</small></span></Link>
      {user && <div className="account-control"><span className="account-name">{user.display_name || user.username}</span>{logoutError && <span className="logout-error" role="alert">登出失敗，請重試</span>}<button type="button" onClick={() => void logout()} disabled={!hydrated || loggingOut}><LogOut size={17} aria-hidden="true" />{loggingOut ? "登出中…" : "登出"}</button></div>}
    </header>
    {children}
    <footer className="footer">STF V3 · 僅供獲授權的工作坊成員使用</footer>
  </>;
}
