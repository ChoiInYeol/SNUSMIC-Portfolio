import { NextResponse, type NextRequest } from "next/server";

// Static files bypass route handlers. Reject writes before file resolution so existing
// and missing v2 resources have the same HTTP semantics (and preflights never return 500).
export function proxy(request: NextRequest) {
  if (request.method === "GET" || request.method === "HEAD") return NextResponse.next();
  const headers = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
    Allow: "GET, HEAD, OPTIONS",
  };
  if (request.method === "OPTIONS") return new NextResponse(null, { status: 204, headers });
  return NextResponse.json(
    {
      type: "about:blank",
      title: "Method Not Allowed",
      status: 405,
      detail: "The API is read-only: use GET, HEAD or OPTIONS.",
      instance: request.nextUrl.pathname,
    },
    { status: 405, headers: { ...headers, "Content-Type": "application/problem+json" } },
  );
}

export const config = { matcher: "/api/v2/:path*" };
