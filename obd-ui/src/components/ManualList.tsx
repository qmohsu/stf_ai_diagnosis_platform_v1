"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  BookOpen,
  CheckCircle,
  Loader2,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { deleteManual, listManuals } from "@/lib/api";
import type { ManualSummary } from "@/lib/types";

interface ManualListProps {
  /** Incremented when the parent wants a refresh. */
  refreshKey: number;
  onSelect: (manualId: string) => void;
}

function statusBadge(status: string, t: (k: string) => string) {
  switch (status) {
    case "ingested":
      return (
        <Badge variant="default" className="gap-1">
          <CheckCircle className="h-3 w-3" />
          {t("manuals.ingested")}
        </Badge>
      );
    case "converting":
      return (
        <Badge variant="secondary" className="gap-1">
          <Loader2 className="h-3 w-3 animate-spin" />
          {t("manuals.converting")}
        </Badge>
      );
    case "failed":
      return (
        <Badge variant="destructive" className="gap-1">
          <AlertTriangle className="h-3 w-3" />
          {t("manuals.failed")}
        </Badge>
      );
    default:
      return <Badge variant="outline">{status}</Badge>;
  }
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

export function ManualList({ refreshKey, onSelect }: ManualListProps) {
  const { t } = useTranslation();
  const [items, setItems] = useState<ManualSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval>>();

  const fetchManuals = useCallback(async () => {
    try {
      const data = await listManuals(100);
      setItems(data.items);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    fetchManuals();
  }, [fetchManuals, refreshKey]);

  // Poll for items in "converting" state.
  useEffect(() => {
    const hasConverting = items.some((m) => m.status === "converting");
    if (hasConverting) {
      pollRef.current = setInterval(fetchManuals, 5000);
    }
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [items, fetchManuals]);

  const handleDelete = useCallback(
    async (id: string, e: React.MouseEvent) => {
      e.stopPropagation();
      if (!confirm(t("manuals.deleteConfirm"))) return;
      setDeleting(id);
      try {
        await deleteManual(id);
        setItems((prev) => prev.filter((m) => m.id !== id));
      } catch (err) {
        setError(err instanceof Error ? err.message : "Delete failed");
      } finally {
        setDeleting(null);
      }
    },
    [t],
  );

  const formatDate = (iso: string) => {
    try {
      return new Date(iso).toLocaleString();
    } catch {
      return iso;
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="h-6 w-6 animate-spin mr-2 text-muted-foreground" />
        <span className="text-sm text-muted-foreground">Loading manuals...</span>
      </div>
    );
  }

  if (error) {
    return (
      <Alert variant="destructive">
        <AlertDescription className="flex items-center justify-between">
          <span>{error}</span>
          <Button variant="outline" size="sm" onClick={fetchManuals}>
            <RefreshCw className="h-4 w-4 mr-1" />
            Retry
          </Button>
        </AlertDescription>
      </Alert>
    );
  }

  if (items.length === 0) {
    return (
      <div className="text-center py-12">
        <BookOpen className="h-10 w-10 text-muted-foreground mx-auto" />
        <p className="mt-2 text-sm text-muted-foreground">
          {t("manuals.noManuals")}
        </p>
      </div>
    );
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>{t("manuals.filename")}</TableHead>
          <TableHead>{t("manuals.vehicleModel")}</TableHead>
          <TableHead>{t("manuals.statusLabel")}</TableHead>
          <TableHead className="text-right">{t("manuals.pages")}</TableHead>
          <TableHead className="text-right">{t("manuals.chunks")}</TableHead>
          <TableHead>{t("manuals.language")}</TableHead>
          <TableHead>{t("manuals.uploaded")}</TableHead>
          <TableHead />
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((m) => (
          <TableRow
            key={m.id}
            className="cursor-pointer hover:bg-muted/50"
            onClick={() => m.status === "ingested" && onSelect(m.id)}
          >
            <TableCell className="font-mono text-sm max-w-[200px] truncate">
              {m.filename}
            </TableCell>
            <TableCell className="text-sm">
              {m.vehicle_model || "-"}
            </TableCell>
            <TableCell>{statusBadge(m.status, t)}</TableCell>
            <TableCell className="text-right text-sm text-muted-foreground">
              {m.page_count ?? "-"}
            </TableCell>
            <TableCell className="text-right text-sm text-muted-foreground">
              {m.chunk_count ?? "-"}
            </TableCell>
            <TableCell className="text-sm text-muted-foreground">
              {m.language || "-"}
            </TableCell>
            <TableCell className="text-sm text-muted-foreground">
              {formatDate(m.created_at)}
            </TableCell>
            <TableCell>
              <Button
                variant="ghost"
                size="sm"
                onClick={(e) => handleDelete(m.id, e)}
                disabled={deleting === m.id}
              >
                {deleting === m.id ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Trash2 className="h-4 w-4 text-muted-foreground hover:text-destructive" />
                )}
              </Button>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
