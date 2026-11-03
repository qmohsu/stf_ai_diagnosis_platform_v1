import type { MetadataRoute } from "next";
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "STF V3 車輛診斷", short_name: "STF V3", description: "工作坊車輛與診斷平台",
    start_url: "/", display: "standalone", background_color: "#f4f7fb", theme_color: "#123d6a", lang: "zh-TW",
    icons: [
      { src: "/icons/icon-192.svg", sizes: "192x192", type: "image/svg+xml", purpose: "any" },
      { src: "/icons/icon-512.svg", sizes: "512x512", type: "image/svg+xml", purpose: "any" },
      { src: "/icons/icon-maskable.svg", sizes: "512x512", type: "image/svg+xml", purpose: "maskable" },
    ],
  };
}
