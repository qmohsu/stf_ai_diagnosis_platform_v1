"use client";

import React, { useCallback, useState } from "react";
import { Upload } from "lucide-react";
import { cn } from "@/lib/utils";

interface FileDropZoneProps {
  onFileContent: (content: string) => void;
  maxSizeMB?: number;
}

export function FileDropZone({ onFileContent, maxSizeMB = 10 }: FileDropZoneProps) {
  const [isDragging, setIsDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleFile = useCallback(
    (file: File) => {
      setError(null);
      if (file.size > maxSizeMB * 1024 * 1024) {
        setError(`File exceeds ${maxSizeMB} MB limit`);
        return;
      }
      const reader = new FileReader();
      reader.onload = (e) => {
        const text = e.target?.result;
        if (typeof text === "string") {
          onFileContent(text);
        }
      };
      reader.onerror = () => setError("Failed to read file");
      reader.readAsText(file);
    },
    [onFileContent, maxSizeMB],
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragging(false);
      const file = e.dataTransfer.files[0];
      if (file) handleFile(file);
    },
    [handleFile],
  );

  const handleInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) handleFile(file);
    },
    [handleFile],
  );

  return (
    <div
      onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
      onDragLeave={() => setIsDragging(false)}
      onDrop={handleDrop}
      className={cn(
        "flex flex-col items-center justify-center rounded-lg border-2 border-dashed p-8 transition-colors cursor-pointer",
        isDragging ? "border-primary bg-primary/5" : "border-muted-foreground/25 hover:border-primary/50",
      )}
      onClick={() => document.getElementById("file-input")?.click()}
    >
      <Upload className="mb-2 h-8 w-8 text-muted-foreground" />
      <p className="text-sm text-muted-foreground">
        Drag and drop a .txt file here, or click to browse
      </p>
      <p className="mt-1 text-xs text-muted-foreground">Max {maxSizeMB} MB</p>
      {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
      <input
        id="file-input"
        type="file"
        accept=".txt,.tsv,.log"
        className="hidden"
        onChange={handleInputChange}
      />
    </div>
  );
}
