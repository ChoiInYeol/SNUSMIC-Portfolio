import { NextResponse } from "next/server";
import { SignalsPayloadSchema } from "@/lib/api-schemas";
import artifact from "../../../../../../public/api/v1/signals/latest.json";

export const dynamic = "force-static";

// Invalid generated input must fail the build, not publish a static cached 503.
const signals = SignalsPayloadSchema.parse(artifact);

// Cache/CORS for this alias must stay identical to /api/v1/signals/latest.json in next.config.ts.
const headers = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
  "X-Content-Type-Options": "nosniff",
  "Cache-Control": "public, max-age=300, stale-while-revalidate=60",
};

export async function OPTIONS() {
  return new NextResponse(null, { status: 204, headers });
}

export async function GET() {
  return NextResponse.json(signals, { headers });
}
