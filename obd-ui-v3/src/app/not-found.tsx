import Link from "next/link";

export default function NotFound() {
  return <main className="not-found"><h1>404</h1><p>找不到頁面</p><Link className="button" href="/">返回首頁</Link></main>;
}
