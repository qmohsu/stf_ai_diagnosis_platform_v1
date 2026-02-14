import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";

const inter = Inter({ subsets: ["latin"] });

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
        <div className="min-h-screen bg-background">
          <header className="border-b bg-card">
            <div className="container mx-auto flex h-14 items-center px-4">
              <h1 className="text-lg font-semibold">
                OBD Expert Diagnostic
              </h1>
              <span className="ml-2 text-xs text-muted-foreground">
                STF AI Diagnosis Platform
              </span>
            </div>
          </header>
          <main className="container mx-auto px-4 py-6">{children}</main>
        </div>
      </body>
    </html>
  );
}
