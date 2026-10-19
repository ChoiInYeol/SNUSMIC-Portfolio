import { NextResponse } from "next/server";
import { SchemaMetadataSchema } from "@/lib/schema-metadata";

export const dynamic = "force-dynamic";

// The browser never receives the database URL or upstream credentials. This sidecar is
// optional for the existing static site; its failure is an explicit error, never a stale graph.
export async function GET() {
  try {
    const upstream = await fetch(process.env.METADATA_API_URL ?? "http://127.0.0.1:8000/metadata/schema", {
      cache: "no-store", signal: AbortSignal.timeout(15_000), headers: { Accept: "application/json" },
    });
    if (!upstream.ok) throw new Error("Metadata service unavailable");
    const doc = SchemaMetadataSchema.parse(await upstream.json());
    return NextResponse.json(doc, { headers: { "Cache-Control": "no-store" } });
  } catch {
    return NextResponse.json({ type: "about:blank", title: "메타데이터 서버에 연결할 수 없습니다", status: 503,
      detail: "FastAPI 서버 실행 상태와 METADATA_API_URL 설정을 확인한 뒤 다시 연결하세요." },
    { status: 503, headers: { "Content-Type": "application/problem+json", "Cache-Control": "no-store" } });
  }
}
