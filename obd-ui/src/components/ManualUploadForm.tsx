"use client";

import { useCallback, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { FileUp, Loader2, Upload, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { uploadManual } from "@/lib/api";

interface ManualUploadFormProps {
  onUploaded: () => void;
}

function formatBytes(bytes: number): string {
  if (bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.min(
    Math.floor(Math.log(bytes) / Math.log(1024)),
    units.length - 1,
  );
  const val = bytes / Math.pow(1024, i);
  return `${val.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

export function ManualUploadForm({ onUploaded }: ManualUploadFormProps) {
  const { t } = useTranslation();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [dragActive, setDragActive] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // APP-59: manufacturer + model are both required.
  const [manufacturer, setManufacturer] = useState("");
  const [vehicleModel, setVehicleModel] = useState("");
  // APP-61: optional factory / manual code alias (e.g. MWS150-A).
  const [factoryCode, setFactoryCode] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);

  const canSubmit =
    !!selectedFile &&
    manufacturer.trim().length > 0 &&
    vehicleModel.trim().length > 0 &&
    !uploading;

  const selectFile = useCallback(
    (file: File) => {
      if (!file.name.toLowerCase().endsWith(".pdf")) {
        setError(t("manuals.unsupportedType"));
        return;
      }
      setError(null);
      setSelectedFile(file);
    },
    [t],
  );

  const handleSubmit = useCallback(async () => {
    if (!selectedFile || !manufacturer.trim() || !vehicleModel.trim()) {
      return;
    }
    setError(null);
    setUploading(true);
    try {
      await uploadManual(
        selectedFile,
        manufacturer.trim(),
        vehicleModel.trim(),
        factoryCode.trim() || undefined,
      );
      setSelectedFile(null);
      setManufacturer("");
      setVehicleModel("");
      setFactoryCode("");
      onUploaded();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  }, [selectedFile, manufacturer, vehicleModel, factoryCode, onUploaded]);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setDragActive(false);
      const file = e.dataTransfer.files[0];
      if (file) selectFile(file);
    },
    [selectFile],
  );

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
  }, []);

  const handleInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) selectFile(file);
      e.target.value = "";
    },
    [selectFile],
  );

  const clearFile = useCallback(() => {
    setSelectedFile(null);
    setError(null);
  }, []);

  return (
    <Card>
      <CardContent className="pt-6 space-y-4">
        {/* Drop zone */}
        <div
          className={`relative flex flex-col items-center justify-center rounded-lg border-2 border-dashed p-8 transition-colors ${
            selectedFile
              ? "border-primary/50 bg-primary/5"
              : dragActive
                ? "border-primary bg-primary/5"
                : "border-muted-foreground/25 hover:border-muted-foreground/50"
          } ${uploading ? "" : "cursor-pointer"}`}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          onClick={() => {
            if (!uploading && !selectedFile) {
              fileInputRef.current?.click();
            }
          }}
        >
          {selectedFile ? (
            <>
              <FileUp className="h-10 w-10 text-primary" />
              <p className="mt-2 text-sm font-medium">
                {selectedFile.name}
              </p>
              <p className="text-xs text-muted-foreground">
                {formatBytes(selectedFile.size)}
              </p>
              {!uploading && (
                <button
                  className="absolute top-3 right-3 p-1 rounded-full hover:bg-muted"
                  onClick={(e) => {
                    e.stopPropagation();
                    clearFile();
                  }}
                >
                  <X className="h-4 w-4 text-muted-foreground" />
                </button>
              )}
            </>
          ) : (
            <>
              <FileUp className="h-10 w-10 text-muted-foreground" />
              <p className="mt-2 text-sm text-muted-foreground">
                {t("manuals.dropzone")}
              </p>
              <Button
                variant="outline"
                size="sm"
                className="mt-3"
                onClick={(e) => {
                  e.stopPropagation();
                  fileInputRef.current?.click();
                }}
              >
                <Upload className="h-4 w-4 mr-2" />
                {t("manuals.selectFile")}
              </Button>
              <p className="text-xs text-muted-foreground mt-2">
                {t("manuals.maxSize", { size: "200" })}
              </p>
            </>
          )}
          <input
            ref={fileInputRef}
            type="file"
            accept=".pdf"
            className="hidden"
            onChange={handleInputChange}
            disabled={uploading}
          />
        </div>

        {/* Manufacturer input (required) */}
        <div className="flex items-center gap-3">
          <label className="text-sm font-medium whitespace-nowrap w-32">
            {t("manuals.manufacturer")}
            <span className="text-destructive"> *</span>
          </label>
          <input
            type="text"
            value={manufacturer}
            onChange={(e) => setManufacturer(e.target.value)}
            placeholder={t("manuals.manufacturerPlaceholder")}
            className="flex-1 rounded-md border px-3 py-1.5 text-sm bg-background"
            disabled={uploading}
          />
        </div>

        {/* Vehicle model input (required) */}
        <div className="flex items-center gap-3">
          <label className="text-sm font-medium whitespace-nowrap w-32">
            {t("manuals.vehicleModel")}
            <span className="text-destructive"> *</span>
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

        {/* Factory code input (APP-61, optional) */}
        <div className="flex items-center gap-3">
          <label className="text-sm font-medium whitespace-nowrap w-32">
            {t("manuals.factoryCode")}
            <span className="text-muted-foreground font-normal">
              {" "}
              ({t("manuals.factoryCodeOptional")})
            </span>
          </label>
          <input
            type="text"
            value={factoryCode}
            onChange={(e) => setFactoryCode(e.target.value)}
            placeholder={t("manuals.factoryCodePlaceholder")}
            className="flex-1 rounded-md border px-3 py-1.5 text-sm bg-background"
            disabled={uploading}
          />
        </div>
        <p className="text-xs text-muted-foreground -mt-2 ml-[8.75rem]">
          {t("manuals.factoryCodeHint")}
        </p>

        {/* Submit button */}
        <Button
          className="w-full"
          disabled={!canSubmit}
          onClick={handleSubmit}
        >
          {uploading ? (
            <>
              <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              {t("manuals.uploading")}
            </>
          ) : (
            <>
              <Upload className="h-4 w-4 mr-2" />
              {t("manuals.upload")}
            </>
          )}
        </Button>

        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
      </CardContent>
    </Card>
  );
}
