"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { CarFront, LogOut, RefreshCw, UserRound } from "lucide-react";
import { Brand } from "@/components/brand";
import { LanguageSwitch } from "@/components/language-switch";
import { useLocale } from "@/components/locale-provider";
import { clearPortalCaches } from "@/lib/client-session";
import type { VehicleVerificationContext } from "@/lib/domain";
import type { PortalUser } from "@/server/auth-session";

export function SiteChrome({ children, user, vehicleContext }: { children: React.ReactNode; user: PortalUser | null; vehicleContext: VehicleVerificationContext | null }) {
  const { messages } = useLocale();
  const pathname = usePathname();
  const onVehiclePage = pathname.startsWith("/vehicles/");
  const loginPage = pathname === "/login";
  const entryPage = loginPage || pathname === "/" || pathname === "/verify-vehicle";
  return <>
    <header className="topbar portal-topbar">
      <Link href="/" className="brand-link" aria-label={`Towngas ${messages.nav.home}`}><Brand compact /></Link>
      <div className="topbar-title"><strong>{messages.brand}</strong><span>{loginPage ? messages.login.title : onVehiclePage ? messages.dashboard.tagline : messages.verification.title}</span></div>
      {onVehiclePage && vehicleContext && <VehicleContextControl context={vehicleContext} />}
      {user && <AccountControl user={user} />}
      <LanguageSwitch />
    </header>
    {children}
    {!entryPage && <footer className="footer"><span>© Towngas</span><span>{messages.publicPortal} · robots: noindex, nofollow</span></footer>}
  </>;
}

function AccountControl({ user }: { user: PortalUser }) {
  const { messages } = useLocale();
  const [loggingOut, setLoggingOut] = useState(false);
  const logout = async () => {
    if (loggingOut) return;
    setLoggingOut(true);
    try {
      await fetch("/api/auth/logout", { method: "POST", headers: { Accept: "application/json" }, credentials: "same-origin", cache: "no-store" });
    } finally {
      await clearPortalCaches();
      window.location.assign("/login");
    }
  };
  return <div className="account-control">
    <span className="account-name"><UserRound aria-hidden="true" /><span><small>{messages.login.signedInAs}</small><strong>{user.display_name || user.username}</strong></span></span>
    <button type="button" onClick={() => void logout()} disabled={loggingOut} aria-label={messages.login.logout}><LogOut aria-hidden="true" /><span>{loggingOut ? messages.login.loggingOut : messages.login.logout}</span></button>
  </div>;
}

function VehicleContextControl({ context }: { context: VehicleVerificationContext }) {
  const { messages } = useLocale();
  const [mounted, setMounted] = useState(false);
  const [clearing, setClearing] = useState(false);
  useEffect(() => setMounted(true), []);

  const clear = async () => {
    setClearing(true);
    try {
      await clearPortalCaches();
      const response = await fetch("/api/portal/verification", { method: "DELETE", headers: { Accept: "application/json" }, credentials: "same-origin", cache: "no-store" });
      window.location.assign(response.ok ? "/" : "/verify-vehicle");
    } catch { window.location.assign("/verify-vehicle"); }
  };

  return <div className="vehicle-context-control">
    <div className="vehicle-context-readout"><CarFront aria-hidden="true" /><span><small>{messages.verification.currentVehicle}</small><strong>{context.manufacturer} {context.model} · {context.vin}</strong></span></div>
    <button type="button" className="vehicle-switch-button" onClick={() => void clear()} disabled={!mounted || clearing}><RefreshCw className={clearing ? "spin" : ""} aria-hidden="true" /><span>{messages.dashboard.switchVehicle}</span></button>
  </div>;
}
