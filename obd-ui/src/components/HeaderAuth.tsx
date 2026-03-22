"use client";

import Link from "next/link";
import { useTranslation } from "react-i18next";
import { useAuth } from "./AuthProvider";
import { LanguageSwitcher } from "./LanguageSwitcher";

export function HeaderAuth() {
  const { username, logout, isLoading } = useAuth();
  const { t } = useTranslation();

  return (
    <div className="ml-auto flex items-center gap-3">
      {!isLoading && username && (
        <Link
          href="/sessions"
          className="text-sm text-muted-foreground hover:text-foreground"
        >
          {t("header.mySessions")}
        </Link>
      )}
      <LanguageSwitcher />
      {!isLoading && username && (
        <>
          <span className="text-sm text-muted-foreground">{username}</span>
          <button
            onClick={logout}
            className="rounded border px-2 py-1 text-xs hover:bg-muted"
          >
            {t("header.logout")}
          </button>
        </>
      )}
    </div>
  );
}
