import type { Metadata, Viewport } from "next";
import "./globals.css";
import "./portal.css";
import { PwaRegister } from "@/components/pwa-register";
import { QueryProvider } from "@/components/query-provider";
import { SiteChrome } from "@/components/site-chrome";
import { currentPortalUserSafely } from "@/server/auth-session";

export const metadata: Metadata = {
  title: { default: "STF V3 車輛診斷", template: "%s · STF V3" },
  description: "STF V3 工作坊車輛與診斷平台",
  manifest: "/manifest.webmanifest",
  icons: {
    icon: [{ url: "/icons/icon-192.svg", sizes: "192x192", type: "image/svg+xml" }],
    apple: "/icons/apple-touch-icon.svg",
  },
  robots: { index: false, follow: false },
};
export const viewport: Viewport = { width: "device-width", initialScale: 1, themeColor: "#123d6a" };

export default async function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const user = await currentPortalUserSafely();
  return (
    <html lang="zh-TW">
      <body><QueryProvider><PwaRegister /><SiteChrome user={user}>{children}</SiteChrome></QueryProvider></body>
    </html>
  );
}
