import type { NextConfig } from "next";

const cors = [
  { key: "Access-Control-Allow-Origin", value: "*" },
  { key: "Access-Control-Allow-Methods", value: "GET, HEAD, OPTIONS" },
  { key: "Access-Control-Allow-Headers", value: "Content-Type" },
];

// Mutable daily artifacts. Keep in sync with src/app/api/v1/signals/latest/route.ts.
const shortCache = { key: "Cache-Control", value: "public, max-age=300, stale-while-revalidate=60" };

const nextConfig: NextConfig = {
  reactStrictMode: true,
  turbopack: {
    root: process.cwd(),
  },
  async headers() {
    return [
      { source: "/api/v1/signals/latest.json", headers: [shortCache, ...cors] },
      { source: "/api/v1/strategy-details/manifest.json", headers: [shortCache, ...cors] },
      {
        // Dated snapshots never change once published, but header rules also match a
        // not-yet-published date's 404 — so browsers revalidate hourly; the CDN cache
        // lives until the next deploy.
        source: "/api/v1/signals/:date([0-9]{4}-[0-9]{2}-[0-9]{2}).json",
        headers: [{ key: "Cache-Control", value: "public, max-age=3600, s-maxage=31536000" }, ...cors],
      },
      {
        // Content-addressed: the hash in the path changes whenever the bytes do.
        source: "/api/v1/strategy-details/:key/:hash([0-9a-f]{64}).json",
        headers: [{ key: "Cache-Control", value: "public, max-age=31536000, immutable" }, ...cors],
      },
      {
        source: "/api/:path*",
        headers: [...cors, { key: "X-Content-Type-Options", value: "nosniff" }],
      },
    ];
  },
};

export default nextConfig;
