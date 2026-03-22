"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useTranslation } from "react-i18next";
import {
  ArrowLeft,
  Check,
  ChevronLeft,
  ChevronRight,
  Loader2,
  RefreshCw,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Select } from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { listSessions } from "@/lib/api";
import type { SessionListItem } from "@/lib/types";

const PAGE_SIZE = 20;

/** Format byte count to human-readable string (e.g., "1.2 KB"). */
function formatBytes(bytes: number): string {
  if (bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(
    Math.floor(Math.log(bytes) / Math.log(1024)),
    units.length - 1,
  );
  const val = bytes / Math.pow(1024, i);
  return `${val.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function statusVariant(
  s: string,
): "default" | "secondary" | "destructive" | "outline" {
  switch (s) {
    case "COMPLETED":
      return "default";
    case "FAILED":
      return "destructive";
    default:
      return "secondary";
  }
}

export default function SessionsPage() {
  const router = useRouter();
  const { t } = useTranslation();
  const [items, setItems] = useState<SessionListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>("all");

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const fetchSessions = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const status = statusFilter === "all" ? undefined : statusFilter;
      const data = await listSessions(PAGE_SIZE, page * PAGE_SIZE, status);
      setItems(data.items);
      setTotal(data.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load sessions");
    } finally {
      setLoading(false);
    }
  }, [page, statusFilter]);

  useEffect(() => {
    fetchSessions();
  }, [fetchSessions]);

  const handlePrev = () => setPage((p) => Math.max(0, p - 1));
  const handleNext = () => setPage((p) => Math.min(totalPages - 1, p + 1));

  const handleStatusChange = (
    e: React.ChangeEvent<HTMLSelectElement>,
  ) => {
    setStatusFilter(e.target.value);
    setPage(0);
  };

  const formatDate = (iso: string) => {
    if (!iso) return "";
    try {
      return new Date(iso).toLocaleString();
    } catch {
      return iso;
    }
  };

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

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div className="space-y-1">
              <CardTitle>{t("sessions.title")}</CardTitle>
              {!loading && (
                <p className="text-sm text-muted-foreground">
                  {t("sessions.sessionCount", { count: total })}
                </p>
              )}
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={fetchSessions}
              disabled={loading}
            >
              <RefreshCw className={`h-4 w-4 mr-1 ${loading ? "animate-spin" : ""}`} />
              {t("sessions.refresh")}
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Filter bar */}
          <div className="flex items-center gap-3">
            <span className="text-sm font-medium">{t("sessions.filterStatus")}:</span>
            <Select className="w-[140px]" value={statusFilter} onChange={handleStatusChange}>
              <option value="all">{t("sessions.filterAll")}</option>
              <option value="COMPLETED">{t("sessions.completed")}</option>
              <option value="PENDING">{t("sessions.pending")}</option>
              <option value="FAILED">{t("sessions.failed")}</option>
            </Select>
          </div>

          {/* Loading state */}
          {loading && (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="h-6 w-6 animate-spin mr-2 text-muted-foreground" />
              <span className="text-sm text-muted-foreground">
                {t("sessions.loading")}
              </span>
            </div>
          )}

          {/* Error state */}
          {!loading && error && (
            <Alert variant="destructive">
              <AlertDescription className="flex items-center justify-between">
                <span>{error}</span>
                <Button variant="outline" size="sm" onClick={fetchSessions}>
                  {t("sessions.retry")}
                </Button>
              </AlertDescription>
            </Alert>
          )}

          {/* Empty state */}
          {!loading && !error && items.length === 0 && (
            <div className="text-center py-12 space-y-3">
              <p className="text-sm text-muted-foreground">
                {t("sessions.empty")}
              </p>
              <Button variant="outline" size="sm" onClick={() => router.push("/")}>
                {t("sessions.backToUpload")}
              </Button>
            </div>
          )}

          {/* Session table */}
          {!loading && !error && items.length > 0 && (
            <>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("sessions.vehicleId")}</TableHead>
                    <TableHead>{t("sessions.filterStatus")}</TableHead>
                    <TableHead>{t("sessions.inputSize")}</TableHead>
                    <TableHead className="text-center">{t("sessions.diagnosis")}</TableHead>
                    <TableHead className="text-center">{t("sessions.premiumDiagnosis")}</TableHead>
                    <TableHead>{t("sessions.createdAt")}</TableHead>
                    <TableHead />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map((session) => (
                    <TableRow
                      key={session.session_id}
                      className="cursor-pointer hover:bg-muted/50"
                      onClick={() => router.push(`/analysis/${session.session_id}`)}
                    >
                      <TableCell className="font-mono text-sm">
                        {session.vehicle_id || t("sessions.noVehicle")}
                      </TableCell>
                      <TableCell>
                        <Badge variant={statusVariant(session.status)}>
                          {t(`sessions.${session.status.toLowerCase()}`)}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-sm text-muted-foreground">
                        {formatBytes(session.input_size_bytes)}
                      </TableCell>
                      <TableCell className="text-center">
                        {session.has_diagnosis ? (
                          <Check className="h-4 w-4 text-green-600 mx-auto" />
                        ) : (
                          <X className="h-4 w-4 text-muted-foreground mx-auto" />
                        )}
                      </TableCell>
                      <TableCell className="text-center">
                        {session.has_premium_diagnosis ? (
                          <Check className="h-4 w-4 text-green-600 mx-auto" />
                        ) : (
                          <X className="h-4 w-4 text-muted-foreground mx-auto" />
                        )}
                      </TableCell>
                      <TableCell className="text-sm text-muted-foreground">
                        {formatDate(session.created_at)}
                      </TableCell>
                      <TableCell>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={(e) => {
                            e.stopPropagation();
                            router.push(`/analysis/${session.session_id}`);
                          }}
                        >
                          {t("sessions.viewSession")}
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>

              {/* Pagination */}
              {totalPages > 1 && (
                <div className="flex items-center justify-between pt-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={handlePrev}
                    disabled={page === 0 || loading}
                  >
                    <ChevronLeft className="h-4 w-4 mr-1" />
                    {t("sessions.previous")}
                  </Button>
                  <span className="text-sm text-muted-foreground">
                    {t("sessions.pageOf", { current: page + 1, total: totalPages })}
                  </span>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={handleNext}
                    disabled={page >= totalPages - 1 || loading}
                  >
                    {t("sessions.next")}
                    <ChevronRight className="h-4 w-4 ml-1" />
                  </Button>
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
