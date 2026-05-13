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
            <div className="flex min-h-screen flex-col bg-background">
              <header className="border-b bg-card">
                <div className="container mx-auto flex h-14 items-center gap-3 px-4">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src="/polyu-logo.png"
                    alt="The Hong Kong Polytechnic University"
                    className="h-10 w-auto shrink-0"
                  />
                  <HeaderTitle />
                  <HeaderAuth />
                </div>
              </header>
              <main className="container mx-auto flex-1 px-4 py-6">
                {children}
              </main>
              <footer className="mt-8 border-t bg-card">
                <div className="container mx-auto flex items-center justify-center px-4 py-6">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src="/polyu-logo-full.png"
                    alt="The Hong Kong Polytechnic University 香港理工大學"
                    className="h-24 w-auto"
                  />
                </div>
              </footer>
            </div>
          </AuthProvider>
        </I18nProvider>
      </body>
    </html>
  );
}
