import { NextResponse, type NextRequest } from "next/server";
import { z } from "zod";
import resourceIndex from "@/data/api-v2-resources.json";

// Static files bypass route handlers, and under `next start` next.config.ts header rules are applied before
// any route runs and win over a route handler's own headers — so a fallback 404 inherits the cache rule of
// the URI it was asked for (a year for an unpublished revision or snapshot). This guard answers before file
// resolution instead: OPTIONS, writes, and every URI that is not a published resource
// (src/data/api-v2-resources.json, written by scripts/export_api_v2.py). Errors are never cached.
// Deprecated /api/v1 gets the same method contract (static files otherwise answer writes with 500) and a
// no-store 404 for a missing dated snapshot or strategy-details revision; its other URIs pass through.

const ResourceIndexSchema = z.object({
  schema_version: z.literal("1.0"),
  prefix: z.literal("/api/v2"),
  resources: z.array(z.string().min(1)).min(1),
});
const index = ResourceIndexSchema.parse(resourceIndex);
const published = new Set(index.resources);

const headers = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
  "X-Content-Type-Options": "nosniff",
  "Cache-Control": "no-store",
};

// `/api/v2` → "index"; `/api/v2/a/b` and `/api/v2/a/b.json` → "a/b"; `%2B` → `+`. Null — never a resource —
// when a segment is undecodable, empty or decodes to `/` (`clubs%2Fewha` is not `clubs/ewha` to the file
// system), and for `/api/v2/index`, which would serve the entry point under a second URI.
function resourceKey(pathname: string): string | null {
  const rest = pathname.slice(index.prefix.length).replace(/^\//, "").replace(/\.json$/, "");
  if (rest === "") return "index";
  if (rest === "index") return null;
  try {
    const segments = rest.split("/").map(decodeURIComponent);
    return segments.some((s) => s === "" || s.includes("/")) ? null : segments.join("/");
  } catch {
    return null;
  }
}

function problem(request: NextRequest, status: 404 | 405, title: string, detail: string) {
  const extra: Record<string, string> = status === 405 ? { Allow: "GET, HEAD, OPTIONS" } : {};
  const init = { status, headers: { ...headers, ...extra, "Content-Type": "application/problem+json" } };
  if (request.method === "HEAD") return new NextResponse(null, init);
  return NextResponse.json({ type: "about:blank", title, status, detail, instance: request.nextUrl.pathname }, init);
}

// The v1 URIs with long-lived cache rules have a v2 twin: the strategy-details alias is rewritten to the
// v2 revision (same bytes), and the dated snapshot histories mirror each other (test_api_v2_contract).
const V1_DETAIL = /^\/api\/v1\/strategy-details\/([^/]+)\/([0-9a-f]{64})\.json$/;
const V1_SNAPSHOT = /^\/api\/v1\/signals\/(\d{4}-\d{2}-\d{2})\.json$/;

function v2Twin(pathname: string): string | null {
  const detail = V1_DETAIL.exec(pathname);
  if (detail) return `${index.prefix}/strategies/${detail[1]}/revisions/${detail[2]}`;
  const snapshot = V1_SNAPSHOT.exec(pathname);
  return snapshot ? `${index.prefix}/signals/${snapshot[1]}` : null;
}

function isPublished(pathname: string): boolean {
  const uri = pathname.startsWith("/api/v1/") ? v2Twin(pathname) : pathname;
  if (uri === null) return true; // other v1 files: Next.js serves them or its own 404 without a cache rule
  const key = resourceKey(uri);
  return key !== null && published.has(key);
}

export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;
  if (request.method === "GET" || request.method === "HEAD") {
    if (isPublished(pathname)) return NextResponse.next();
    const version = pathname.startsWith("/api/v1/") ? "/api/v1" : "/api/v2";
    return problem(request, 404, "Not Found", `No ${version} resource at this URI. Start from /api/v2 and follow links.`);
  }
  if (request.method === "OPTIONS") {
    return new NextResponse(null, { status: 204, headers: { ...headers, Allow: "GET, HEAD, OPTIONS" } });
  }
  return problem(request, 405, "Method Not Allowed", "The API is read-only: use GET, HEAD or OPTIONS.");
}

export const config = { matcher: ["/api/v2/:path*", "/api/v1/:path*"] };
