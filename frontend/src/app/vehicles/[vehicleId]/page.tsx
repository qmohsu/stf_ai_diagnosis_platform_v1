import { notFound, redirect } from "next/navigation";
import { VehicleDetails } from "@/components/vehicle-details";
import { currentPortalSession } from "@/server/auth-session";
import { UUID_RE } from "@/server/v3-client";

export default async function VehiclePage({ params }: { params: Promise<{ vehicleId: string }> }) {
  const { vehicleId } = await params;
  if (!UUID_RE.test(vehicleId)) notFound();
  let session;
  try { session = await currentPortalSession(); }
  catch { return <main className="page-shell"><div className="state-card" role="alert"><h1>服務暫時無法使用</h1><p>請稍後重新整理頁面。</p></div></main>; }
  if (!session) redirect("/login");
  return <VehicleDetails vehicleId={vehicleId} />;
}
