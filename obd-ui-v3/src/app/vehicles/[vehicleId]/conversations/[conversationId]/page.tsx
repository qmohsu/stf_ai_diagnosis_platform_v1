import { notFound, redirect } from "next/navigation";
import { ConversationView } from "@/components/conversation-view";
import { currentPortalSession } from "@/server/auth-session";
import { UUID_RE } from "@/server/v3-client";

export default async function ConversationPage({ params }: { params: Promise<{ vehicleId: string; conversationId: string }> }) {
  const { vehicleId, conversationId } = await params;
  if (!UUID_RE.test(vehicleId) || !UUID_RE.test(conversationId)) notFound();
  let session;
  try { session = await currentPortalSession(); }
  catch { return <main className="page-shell"><div className="state-card" role="alert"><h1>服務暫時無法使用</h1><p>請稍後重新整理頁面。</p></div></main>; }
  if (!session) redirect("/login");
  return <ConversationView vehicleId={vehicleId} conversationId={conversationId} />;
}
