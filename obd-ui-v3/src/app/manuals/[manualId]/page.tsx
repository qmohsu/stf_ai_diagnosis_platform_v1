import { notFound, redirect } from "next/navigation";
import { ManualSectionView } from "@/components/manual-section-view";
import { currentPortalSession } from "@/server/auth-session";
import { UUID_RE } from "@/server/v3-client";

export default async function ManualPage({ params, searchParams }: {
  params: Promise<{ manualId: string }>;
  searchParams: Promise<{ section?: string | string[] }>;
}) {
  const [{ manualId }, query] = await Promise.all([params, searchParams]);
  const section = typeof query.section === "string" ? query.section.trim() : "";
  if (!UUID_RE.test(manualId) || !section || section.length > 500) notFound();
  let session;
  try { session = await currentPortalSession(); }
  catch { return <main className="page-shell"><div className="state-card" role="alert"><h1>服務暫時無法使用</h1><p>請稍後重新整理頁面。</p></div></main>; }
  if (!session) redirect("/login");
  return <ManualSectionView manualId={manualId} section={section} />;
}
