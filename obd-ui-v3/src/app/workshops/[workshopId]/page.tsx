import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { WorkshopVehicles } from "@/components/workshop-vehicles";
import { CreateVehicleForm } from "@/components/create-vehicle-form";
import { currentPortalSession } from "@/server/auth-session";
import { UUID_RE } from "@/server/v3-client";

export default async function WorkshopPage({ params }: { params: Promise<{ workshopId: string }> }) {
  const { workshopId } = await params;
  if (!UUID_RE.test(workshopId)) notFound();
  let session;
  try { session = await currentPortalSession(); }
  catch { return <main className="page-shell"><div className="state-card" role="alert"><h1>服務暫時無法使用</h1><p>請稍後重新整理頁面。</p></div></main>; }
  if (!session) redirect("/login");
  const workshop = session.user.memberships.find((item) => item.workshop_id === workshopId);
  if (!workshop) notFound();
  return <main className="page-shell"><Link className="back-link" href="/">← 我的工作坊</Link><div className="page-heading"><span className="eyebrow">工作坊</span><h1>{workshop.workshop_name}</h1><p>檢視或建立這個工作坊的車輛檔案。</p></div><CreateVehicleForm workshopId={workshopId} /><div className="vehicle-sections"><WorkshopVehicles workshopId={workshopId} /></div></main>;
}
