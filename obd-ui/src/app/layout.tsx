import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { AuthProvider } from "@/components/AuthProvider";
import { I18nProvider } from "@/components/I18nProvider";
import { HeaderAuth } from "@/components/HeaderAuth";
import { HeaderTitle } from "@/components/HeaderTitle";

const inter = Inter({
  subsets: ["latin"],
  fallback: [
    "PingFang SC", "PingFang TC",
    "Microsoft YaHei", "Microsoft JhengHei",
    "Noto Sans CJK SC", "Noto Sans CJK TC",
    "sans-serif",
  ],
});

export const metadata: Metadata = {
  title: "OBD Expert Diagnostic",
  description: "STF AI Diagnosis Platform - OBD Log Analysis",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className={inter.className}>
        <I18nProvider>
          <AuthProvider>
            <div className="min-h-screen bg-background">
              <header className="border-b bg-card">
                <div className="container mx-auto flex h-14 items-center px-4">
                  <HeaderTitle />
                  <HeaderAuth />
                </div>
              </header>
              <main className="container mx-auto px-4 py-6">{children}</main>
            </div>
          </AuthProvider>
        </I18nProvider>
      </body>
    </html>
  );
}
