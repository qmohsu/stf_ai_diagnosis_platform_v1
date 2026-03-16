"use client";

import { useTranslation } from "react-i18next";

export function HeaderTitle() {
  const { t } = useTranslation();

  return (
    <>
      <h1 className="text-lg font-semibold">
        {t("header.title")}
      </h1>
      <span className="ml-2 text-xs text-muted-foreground">
        {t("header.subtitle")}
      </span>
    </>
  );
}
