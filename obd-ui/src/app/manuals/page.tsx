"use client";

import { useState } from "react";
import Link from "next/link";
import { useTranslation } from "react-i18next";
import { ArrowLeft } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ManualUploadForm } from "@/components/ManualUploadForm";
import { ManualList } from "@/components/ManualList";
import { ManualViewer } from "@/components/ManualViewer";

export default function ManualsPage() {
  const { t } = useTranslation();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  if (selectedId) {
    return (
      <div className="container mx-auto px-4 py-6">
        <ManualViewer
          manualId={selectedId}
          onBack={() => setSelectedId(null)}
        />
      </div>
    );
  }

  return (
    <div className="container mx-auto px-4 py-6 space-y-4">
      <div className="flex items-center gap-2">
        <Link
          href="/"
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" />
          {t("sessions.backToUpload")}
        </Link>
      </div>

      {/* Upload section */}
      <ManualUploadForm
        onUploaded={() => setRefreshKey((k) => k + 1)}
      />

      {/* Manual library */}
      <Card>
        <CardHeader>
          <CardTitle>{t("manuals.title")}</CardTitle>
        </CardHeader>
        <CardContent>
          <ManualList
            refreshKey={refreshKey}
            onSelect={setSelectedId}
          />
        </CardContent>
      </Card>
    </div>
  );
}
