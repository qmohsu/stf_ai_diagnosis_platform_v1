import type { NextConfig } from "next";

const standalone = process.env.PORTAL_STANDALONE === "1";

const nextConfig: NextConfig = {
  output: standalone ? "standalone" : undefined,
  poweredByHeader: false,
  reactStrictMode: true,
  devIndicators: false,
  async redirects() {
    return [
      { source: "/vehicles/plate/:identity", destination: "/verify-vehicle", permanent: false },
      { source: "/vehicles/vin/:identity", destination: "/verify-vehicle", permanent: false },
      { source: "/vehicles/plate/:identity/components/:componentCode", destination: "/verify-vehicle", permanent: false },
      { source: "/vehicles/vin/:identity/components/:componentCode", destination: "/verify-vehicle", permanent: false },
    ];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "no-referrer" },
          {
            key: "Permissions-Policy",
            value: "camera=(), microphone=(), geolocation=()",
          },
          {
            key: "Content-Security-Policy",
            value:
              `default-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'; object-src 'none'; img-src 'self' data:; font-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'${process.env.NODE_ENV === "development" ? " 'unsafe-eval'" : ""}; connect-src 'self'; worker-src 'self'; manifest-src 'self'`,
          },
          { key: "X-Robots-Tag", value: "noindex, nofollow" },
        ],
      },
    ];
  },
};

export default nextConfig;
