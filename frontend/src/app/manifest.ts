import type { MetadataRoute } from "next";
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Towngas 車輛健康門戶", short_name: "Towngas 車輛", description: "公開只讀車輛健康資料門戶",
    start_url: "/", display: "standalone", background_color: "#f5f7f5", theme_color: "#007a68", lang: "zh-HK",
    icons: [
      { src: "/icons/icon-192.svg", sizes: "192x192", type: "image/svg+xml", purpose: "any" },
      { src: "/icons/icon-512.svg", sizes: "512x512", type: "image/svg+xml", purpose: "any" },
      { src: "/icons/icon-maskable.svg", sizes: "512x512", type: "image/svg+xml", purpose: "maskable" },
    ],
  };
}
