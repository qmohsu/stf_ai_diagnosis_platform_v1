import Link from "next/link";
import { redirect } from "next/navigation";
import { currentPortalSession } from "@/server/auth-session";

export default async function IndexPage() {
  let session;
  try { session = await currentPortalSession(); }
  catch { return <main className="page-shell"><div className="state-card" role="alert"><h1>服務暫時無法使用</h1><p>無法連接 V3 服務，請稍後重新整理頁面。</p></div></main>; }
  if (!session) redirect("/login");
  const workshops = [...session.user.memberships].sort((a, b) => a.workshop_name.localeCompare(b.workshop_name, "zh-Hant"));
  return <main className="page-shell">
    <div className="page-heading"><span className="eyebrow">STF V3</span><h1>我的工作坊</h1><p>選擇工作坊，查看已建立的車輛檔案。</p></div>
    {workshops.length === 0 ? <div className="state-card"><h2>尚未加入工作坊</h2><p>請聯絡管理員確認帳號所屬的工作坊。</p></div> :
      <div className="workshop-grid">{workshops.map((workshop) => <Link className="workshop-card" href={`/workshops/${workshop.workshop_id}`} key={workshop.workshop_id}>
        <span className="workshop-icon" aria-hidden="true">W</span><span><strong>{workshop.workshop_name}</strong><small>{workshop.role === "manager" ? "管理員" : "維修技師"}</small></span><span aria-hidden="true">→</span>
      </Link>)}</div>}
  </main>;
}
