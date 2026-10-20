import type { NextConfig } from "next";

const cors = [
  { key: "Access-Control-Allow-Origin", value: "*" },
  { key: "Access-Control-Allow-Methods", value: "GET, HEAD, OPTIONS" },
  { key: "Access-Control-Allow-Headers", value: "Content-Type" },
];

// Mutable daily artifacts. Keep in sync with src/app/api/v1/signals/latest/route.ts.
const shortCache = { key: "Cache-Control", value: "public, max-age=300, stale-while-revalidate=60" };
// v2 mutable resources: fresh for 5 minutes, then served stale while revalidating for up to a day
// (data changes once per trading day, so a day of staleness during an outage beats an error).
const dailyCache = { key: "Cache-Control", value: "public, max-age=300, stale-while-revalidate=86400" };
// Dated snapshots never change once published; browsers still revalidate hourly as a safety margin and
// the CDN cache lives until the next deploy. Header rules apply before routes and win over a route
// handler's headers, so v2 404s are answered by src/proxy.ts (no-store) before these rules can matter.
const snapshotCache = { key: "Cache-Control", value: "public, max-age=3600, s-maxage=31536000" };
// Content-addressed: the hash in the path changes whenever the bytes do.
const immutableCache = { key: "Cache-Control", value: "public, max-age=31536000, immutable" };

// /api/v1 is frozen and deprecated (RFC 9745 Deprecation: @<unix seconds> = 2026-10-19T00:00:00Z).
const deprecation = [
  { key: "Deprecation", value: "@1792368000" },
  { key: "Link", value: '</api/v2>; rel="successor-version"' },
];

const DATE = ":date(\\d{4}-\\d{2}-\\d{2})";
const SHA256 = ":sha([0-9a-f]{64})";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Bound static-generation workers as well as the parent process's CPU affinity.
  experimental: { cpus: 4 },
  turbopack: {
    root: process.cwd(),
  },
  async redirects() {
    // Pre-v2 chart assets → their v2 resources (308 keeps the method).
    return [
      { source: "/prices/:id.json", destination: "/api/v2/instruments/:id/prices", permanent: true },
      { source: "/strategy-marks/:id.json", destination: "/api/v2/instruments/:id/trade-marks", permanent: true },
    ];
  },
  async rewrites() {
    // afterFiles: real files in public/ (e.g. /api/v2/openapi.json, *.json direct access) win first.
    return [
      { source: "/api/v2", destination: "/api/v2/index.json" },
      // Extensionless URI → its static file; requests already ending in .json are left alone.
      { source: "/api/v2/:path((?!.*\\.json$).+)", destination: "/api/v2/:path.json" },
      // v1 strategy-detail URLs are served from the identical v2 revision bytes (no duplicate files).
      {
        source: `/api/v1/strategy-details/:key/${SHA256}.json`,
        destination: "/api/v2/strategies/:key/revisions/:sha.json",
      },
    ];
  },
  async headers() {
    // Header rules match the request path (before rewrites); a later rule overrides an earlier one per key.
    return [
      { source: "/api/v1/signals/latest.json", headers: [shortCache, ...cors] },
      { source: "/api/v1/strategy-details/manifest.json", headers: [shortCache, ...cors] },
      { source: `/api/v1/signals/${DATE}.json`, headers: [snapshotCache, ...cors] },
      { source: `/api/v1/strategy-details/:key/${SHA256}.json`, headers: [immutableCache, ...cors] },
      { source: "/api/v1/:path*", headers: deprecation },
      {
        source: "/api/:path*",
        headers: [...cors, { key: "X-Content-Type-Options", value: "nosniff" }],
      },
      { source: "/api/v2", headers: [dailyCache] },
      { source: "/api/v2/:path*", headers: [dailyCache] },
      { source: `/api/v2/signals/${DATE}`, headers: [snapshotCache] },
      { source: `/api/v2/signals/${DATE}.json`, headers: [snapshotCache] },
      {
        source: `/api/v2/strategies/:id/revisions/${SHA256}`,
        headers: [immutableCache, { key: "Link", value: '</api/v2/strategies/:id>; rel="up"' }],
      },
      {
        source: `/api/v2/strategies/:id/revisions/${SHA256}.json`,
        headers: [immutableCache, { key: "Link", value: '</api/v2/strategies/:id>; rel="up"' }],
      },
    ];
  },
};

export default nextConfig;
