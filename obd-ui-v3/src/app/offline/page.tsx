import Link from "next/link";
import { CloudOff } from "lucide-react";
export default function OfflinePage() { return <main className="offline-page"><CloudOff aria-hidden="true" /><h1>目前離線</h1><p>請檢查網絡連線。車輛資料需要重新連線並驗證登入狀態後才能開啟。</p><Link className="button" href="/login">返回登入</Link></main>; }
