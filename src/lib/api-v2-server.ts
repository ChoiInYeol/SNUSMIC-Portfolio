import { promises as fs } from "node:fs";
import path from "node:path";
import type { z } from "zod";
import { SignalIndexDocumentSchema, SignalSnapshotDocumentSchema, type SignalIndexDocument } from "@/lib/api-v2";

// Build-time readers for public/api/v2 (Server Components only). A v2 URI `/api/v2/<p>` is the file
// `public/api/v2/<p>.json` — the same mapping next.config.ts rewrites to. Invalid documents throw so the
// build fails (AGENTS rule 3); only a missing optional resource resolves to null.

const API_ROOT = path.join(process.cwd(), "public", "api", "v2");

function fileFor(uri: string): string {
  const rest = uri.replace(/^\/api\/v2\/?/, "");
  if (rest.includes("..")) throw new Error(`Refusing v2 path outside the API root: ${uri}`);
  // `/api/v2/openapi.json` (and any direct `.json` URI) already names its file.
  return path.join(API_ROOT, rest.endsWith(".json") ? rest : `${rest || "index"}.json`);
}

/** Reads and validates a v2 document; throws when it is missing or does not match its schema. */
export async function readV2<S extends z.ZodType>(uri: string, schema: S): Promise<z.infer<S>> {
  const raw = await fs.readFile(fileFor(uri), "utf-8");
  const parsed = schema.safeParse(JSON.parse(raw));
  if (!parsed.success) throw new Error(`${uri} does not match its v2 schema: ${parsed.error.message}`);
  return parsed.data;
}

/** Whether a v2 resource is published (i.e. GET would not 404). */
export async function v2Exists(uri: string): Promise<boolean> {
  try {
    await fs.access(fileFor(uri));
    return true;
  } catch {
    return false;
  }
}

/** Like readV2, but a resource that does not exist (404 over HTTP) resolves to null. */
export async function readV2Optional<S extends z.ZodType>(uri: string, schema: S): Promise<z.infer<S> | null> {
  return (await v2Exists(uri)) ? readV2(uri, schema) : null;
}

export type SnapshotIndexEntry = SignalIndexDocument["data"][number];

/** Snapshot index, newest first. */
export async function loadSnapshotIndex(): Promise<SnapshotIndexEntry[]> {
  const doc = await readV2("/api/v2/signals", SignalIndexDocumentSchema);
  return [...doc.data].sort((a, b) => b.date.localeCompare(a.date));
}

export const loadSnapshot = (date: string) => readV2(`/api/v2/signals/${date}`, SignalSnapshotDocumentSchema);
export const loadLatestSnapshot = () => readV2("/api/v2/signals/latest", SignalSnapshotDocumentSchema);
