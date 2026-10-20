import { NextResponse, type NextRequest } from "next/server";

// Fallback for /api/v2 URIs that no static file answers. src/proxy.ts already answers unknown URIs (also the
// v1 strategy-details alias of a missing revision) and write methods, so this is reached only when
// src/data/api-v2-resources.json lists a file that is not deployed.
// Note: next.config.ts header rules win over the headers below (Next.js only adds absent headers).

const headers = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
  "X-Content-Type-Options": "nosniff",
  "Cache-Control": "no-store",
};

function problem(request: NextRequest, status: 404 | 405, title: string, detail: string) {
  // The rewrite appends .json; report the URI the client asked for.
  const instance = request.nextUrl.pathname.replace(/\.json$/, "");
  return NextResponse.json(
    { type: "about:blank", title, status, detail, instance },
    {
      status,
      headers: {
        ...headers,
        "Content-Type": "application/problem+json",
        ...(status === 405 ? { Allow: "GET, HEAD, OPTIONS" } : {}),
      },
    },
  );
}

const notFound = (request: NextRequest) =>
  problem(request, 404, "Not Found", "No /api/v2 resource at this URI. Start from /api/v2 and follow links.");

const readOnly = (request: NextRequest) =>
  problem(request, 405, "Method Not Allowed", "The API is read-only: use GET, HEAD or OPTIONS.");

export const GET = notFound;
export const HEAD = notFound;
export const POST = readOnly;
export const PUT = readOnly;
export const PATCH = readOnly;
export const DELETE = readOnly;

export async function OPTIONS() {
  return new NextResponse(null, { status: 204, headers });
}
