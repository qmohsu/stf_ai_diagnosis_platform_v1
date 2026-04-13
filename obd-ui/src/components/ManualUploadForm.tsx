"use client";

import { useCallback, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { FileUp, Loader2, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { uploadManual } from "@/lib/api";

interface ManualUploadFormProps {
  onUploaded: () => void;
}

export function ManualUploadForm({ onUploaded }: ManualUploadFormProps) {
  const { t } = useTranslation();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [dragActive, setDragActive] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [vehicleModel, setVehicleModel] = useState("");

  const handleFile = useCallback(
    async (file: File) => {
      if (!file.name.toLowerCase().endsWith(".pdf")) {
        setError(t("manuals.unsupportedType"));
        return;
      }
      setError(null);
      setUploading(true);
      try {
        await uploadManual(file, vehicleModel || undefined);
        setVehicleModel("");
        onUploaded();
      } catch (err) {
        setError(err instanceof Error ? err.message : "Upload failed");
      } finally {
        setUploading(false);
      }
    },
    [vehicleModel, onUploaded, t],
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragActive(false);
      const file = e.dataTransfer.files[0];
      if (file) handleFile(file);
    },
    [handleFile],
  );

  const handleInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) handleFile(file);
      e.target.value = "";
    },
    [handleFile],
  );

  return (
    <Card>
      <CardContent className="pt-6 space-y-4">
        {/* Drop zone */}
        <div
          className={`relative flex flex-col items-center justify-center rounded-lg border-2 border-dashed p-8 transition-colors cursor-pointer ${
            dragActive
              ? "border-primary bg-primary/5"
              : "border-muted-foreground/25 hover:border-muted-foreground/50"
          }`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragActive(true);
          }}
          onDragLeave={() => setDragActive(false)}
          onDrop={handleDrop}
          onClick={() => fileInputRef.current?.click()}
        >
          {uploading ? (
            <Loader2 className="h-10 w-10 animate-spin text-muted-foreground" />
          ) : (
            <FileUp className="h-10 w-10 text-muted-foreground" />
          )}
          <p className="mt-2 text-sm text-muted-foreground">
            {uploading ? t("manuals.uploading") : t("manuals.dropzone")}
          </p>
          <Button
            variant="outline"
            size="sm"
            className="mt-3"
            disabled={uploading}
            onClick={(e) => {
              e.stopPropagation();
              fileInputRef.current?.click();
            }}
          >
            <Upload className="h-4 w-4 mr-2" />
            {t("manuals.upload")}
          </Button>
          <p className="text-xs text-muted-foreground mt-2">
            {t("manuals.maxSize", { size: "200" })}
          </p>
          <input
            ref={fileInputRef}
            type="file"
            accept=".pdf"
            className="hidden"
            onChange={handleInputChange}
            disabled={uploading}
          />
        </div>

        {/* Vehicle model input */}
        <div className="flex items-center gap-3">
          <label className="text-sm font-medium whitespace-nowrap">
            {t("manuals.vehicleModel")}
          </label>
          <input
            type="text"
            value={vehicleModel}
            onChange={(e) => setVehicleModel(e.target.value)}
            placeholder={t("manuals.vehicleModelPlaceholder")}
            className="flex-1 rounded-md border px-3 py-1.5 text-sm bg-background"
            disabled={uploading}
          />
        </div>

        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
      </CardContent>
    </Card>
  );
}
